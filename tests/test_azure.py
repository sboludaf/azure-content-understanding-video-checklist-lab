"""Offline protocol/security checks. Every HTTP request uses MockTransport."""
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx

from video_checklist.azure import API_VERSION, AzureAdapter, AzureConfig, AzureError, TOKEN_SCOPE, build_analyzer_definition, evidence_field_name, validate_endpoint

TENANT = "11111111-1111-1111-1111-111111111111"
PRINCIPAL = "22222222-2222-2222-2222-222222222222"
ENDPOINT = "https://lab.services.ai.azure.com"
LOCATION = ENDPOINT + "/contentunderstanding/analyzerResults/op-123?api-version=" + API_VERSION
STEPS = [{"id": "s1", "label": "Preparar la cuerda"}, {"id": "s2", "label": "Formar el bucle"}, {"id": "s3", "label": "Ajustar el nudo"}]


class Credential:
    def __init__(self, tenant=TENANT, principal=PRINCIPAL):
        claims = {"tid": tenant, "oid": principal, "aud": "https://cognitiveservices.azure.com"}
        payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
        self.token = "header." + payload + ".signature"
        self.scopes = []

    def get_token(self, scope):
        self.scopes.append(scope)
        return SimpleNamespace(token=self.token)


def config():
    prep = build_analyzer_definition(STEPS, "gpt-4.1")
    return AzureConfig(ENDPOINT, TENANT, PRINCIPAL, prep["analyzer_id"], "gpt-4.1", prep["schema_hash"])


def succeeded():
    return {"status": "Succeeded", "id": "op-123", "result": {
        "apiVersion": API_VERSION, "analyzerId": config().analyzer_id,
        "createdAt": "2026-10-12T19:00:00Z", "contents": []},
        "usage": {"videoHours": 0.01, "tokens": {"gpt-4.1-input": 123}}}


class AzureTests(unittest.TestCase):
    def adapter(self, handler, credential=None, **kwargs):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)
        return AzureAdapter(config(), client=client, credential=credential or Credential(), sleep=lambda _: None, authorize=kwargs.pop("authorize", lambda: None), **kwargs)

    def test_authorization_revoked_after_submit_stops_polling_without_resubmit(self):
        sent = []
        approved = [True]
        def authorize():
            if not approved[0]:
                raise ValueError("revoked")
        def handler(request):
            sent.append(request)
            approved[0] = False
            return httpx.Response(202, headers={"Operation-Location": LOCATION})
        adapter = self.adapter(handler, authorize=authorize)
        with self.assertRaises(AzureError) as error:
            adapter.analyze(self.video(), STEPS, {"authorization_confirmed": True, "reservation_id": "r1"})
        self.assertEqual([r.method for r in sent], ["POST"])
        self.assertTrue(error.exception.uncertain)
        self.assertEqual(error.exception.operation_location, LOCATION)

    def video(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "clip.mp4"
        path.write_bytes(b"offline-video-bytes")
        return path

    def test_endpoint_blocks_arbitrary_network_targets(self):
        for endpoint in ("http://lab.services.ai.azure.com", "https://lab.services.ai.azure.com.evil.example",
                         "https://user:pass@lab.services.ai.azure.com", "https://lab.services.ai.azure.com/a",
                         "https://lab.services.ai.azure.com?token=secret", "https://localhost",
                         "https://services.ai.azure.com", "https://lab.services.ai.azure.com:9443"):
            with self.subTest(endpoint=endpoint), self.assertRaises(AzureError):
                validate_endpoint(endpoint)
        self.assertEqual(validate_endpoint("https://lab.cognitiveservices.azure.com/"), "https://lab.cognitiveservices.azure.com")

    def test_schema_hash_does_not_encode_expected_order(self):
        forward = build_analyzer_definition(STEPS, "gpt-4.1")
        backward = build_analyzer_definition(list(reversed(STEPS)), "gpt-4.1")
        self.assertEqual(forward, backward)
        body = json.dumps(forward)
        self.assertNotIn("gold", body)
        self.assertNotIn("knowledgeSources", body)
        self.assertEqual(forward["definition"]["baseAnalyzerId"], "prebuilt-video")

    def test_schema_identity_covers_complete_canonical_definition(self):
        prepared = build_analyzer_definition(STEPS, "gpt-4.1", "geography")
        # Recalculate independently from the actual payload: authorization is
        # tied to every field/configuration value, not a prompt or model subset.
        encoded = json.dumps(prepared["definition"], sort_keys=True,
                             separators=(",", ":"), ensure_ascii=False).encode()
        digest = hashlib.sha256(encoded).hexdigest()
        self.assertEqual(prepared["schema_hash"], digest)
        self.assertEqual(prepared["analyzer_id"], "manual_actions_" + digest[:24])
        different_location = build_analyzer_definition(STEPS, "gpt-4.1", "dataZone")
        self.assertNotEqual(prepared["schema_hash"], different_location["schema_hash"])
        self.assertNotEqual(prepared["analyzer_id"], different_location["analyzer_id"])
        definition = deepcopy(different_location["definition"])
        definition["processingLocation"] = "geography"
        self.assertEqual(prepared["definition"], definition)

    def test_only_candidate_ids_and_labels_enter_vocabulary(self):
        annotated = [dict(step, gold_start_ms=543210 + index,
                          expected_order=900 + index, private_evidence="DO_NOT_SEND")
                     for index, step in enumerate(STEPS)]
        original = deepcopy(annotated)
        prepared = build_analyzer_definition(annotated, "gpt-4.1")
        self.assertEqual(prepared, build_analyzer_definition(STEPS, "gpt-4.1"))
        self.assertEqual(annotated, original)
        body = json.dumps(prepared)
        for prohibited in ("gold_start_ms", "expected_order", "private_evidence", "DO_NOT_SEND", "543210"):
            self.assertNotIn(prohibited, body)

    def test_video_extraction_schema_avoids_rejected_unrouted_category(self):
        definition = build_analyzer_definition(STEPS, "gpt-4.1-mini")["definition"]
        # The real GA 400 identified contentCategories without analyzerId as
        # invalid for video. The tutorial extraction route needs no category.
        self.assertTrue(definition["config"]["returnDetails"])
        self.assertNotIn("enableSegment", definition["config"])
        self.assertNotIn("contentCategories", definition["config"])
        candidate_fields = definition["fieldSchema"]["fields"]
        self.assertEqual(set(candidate_fields), {evidence_field_name(step) for step in STEPS})
        self.assertEqual(list(candidate_fields), sorted(candidate_fields))
        body = json.dumps(definition)
        for step in STEPS:
            self.assertNotIn(json.dumps(step["id"]), body)
        for candidate in candidate_fields.values():
            self.assertEqual(candidate["type"], "array")
            self.assertEqual(candidate["items"]["type"], "object")
            fields = candidate["items"]["properties"]
            self.assertEqual(set(fields), {"SegmentId", "Action", "Objects",
                                          "EvidenceStatus", "EstimatedStartMs", "EstimatedEndMs"})
            self.assertNotIn("StepId", fields)
        self.assertEqual(definition["models"], {"completion": "gpt-4.1-mini"})

    def test_extraction_contract_is_independent_of_vocabulary_and_input_order(self):
        alternative = [{"id": "Z", "label": "Desplazar una caja"},
                       {"id": "A", "label": "Insertar una pieza"},
                       {"id": "M", "label": "Girar una perilla"}]
        first = build_analyzer_definition(STEPS, "gpt-4.1")["definition"]
        second = build_analyzer_definition(alternative, "gpt-4.1")["definition"]
        reordered = build_analyzer_definition(list(reversed(alternative)), "gpt-4.1")
        self.assertEqual(reordered, build_analyzer_definition(alternative, "gpt-4.1"))
        first = deepcopy(first)
        second = deepcopy(second)
        first_vocab = first["fieldSchema"].pop("fields")
        second_vocab = second["fieldSchema"].pop("fields")
        self.assertEqual(first, second)
        self.assertEqual(set(first_vocab), {evidence_field_name(step) for step in STEPS})
        self.assertEqual(set(second_vocab), {evidence_field_name(step) for step in alternative})
        common_properties = first_vocab[evidence_field_name(STEPS[0])]["items"]
        for vocabulary, candidates in ((first_vocab, STEPS), (second_vocab, alternative)):
            for step in candidates:
                candidate = vocabulary[evidence_field_name(step)]
                self.assertEqual(candidate["items"], common_properties)
                self.assertIn(step["label"], candidate["description"])
                for other in candidates:
                    if other["id"] != step["id"]:
                        self.assertNotIn(other["label"], candidate["description"])

    def test_candidate_description_change_is_isolated_to_its_field(self):
        changed = deepcopy(STEPS)
        changed[1]["label"] = "Presionar un botón rojo"
        first = build_analyzer_definition(STEPS, "gpt-4.1")
        second = build_analyzer_definition(changed, "gpt-4.1")
        self.assertNotEqual(first["schema_hash"], second["schema_hash"])
        original_fields = first["definition"]["fieldSchema"]["fields"]
        changed_fields = second["definition"]["fieldSchema"]["fields"]
        self.assertEqual(original_fields[evidence_field_name(STEPS[0])], changed_fields[evidence_field_name(changed[0])])
        self.assertEqual(original_fields[evidence_field_name(STEPS[2])], changed_fields[evidence_field_name(changed[2])])
        self.assertNotEqual(original_fields[evidence_field_name(STEPS[1])]["description"], changed_fields[evidence_field_name(changed[1])]["description"])
        self.assertEqual(original_fields[evidence_field_name(STEPS[1])]["items"], changed_fields[evidence_field_name(changed[1])]["items"])

    def test_model_change_requires_new_identity_with_same_field_contract(self):
        mini = build_analyzer_definition(STEPS, "gpt-4.1-mini")
        full = build_analyzer_definition(STEPS, "gpt-4.1")
        self.assertNotEqual(mini["schema_hash"], full["schema_hash"])
        self.assertNotEqual(mini["analyzer_id"], full["analyzer_id"])
        definition = deepcopy(full["definition"])
        definition["models"] = mini["definition"]["models"]
        self.assertEqual(definition, mini["definition"])

    def test_schema_generation_uses_no_local_dataset_or_evidence(self):
        with patch("builtins.open", side_effect=AssertionError("Analyzer preparation must not read a dataset")), \
             patch.object(Path, "read_text", side_effect=AssertionError("Analyzer preparation must not read evidence")), \
             patch.object(Path, "read_bytes", side_effect=AssertionError("Analyzer preparation must not read evidence")):
            first = build_analyzer_definition(STEPS, "gpt-4.1")
            second = build_analyzer_definition(STEPS, "gpt-4.1")
        self.assertEqual(first, second)
        definition = first["definition"]
        self.assertEqual(definition["config"], {"returnDetails": True})
        self.assertEqual(set(definition), {"description", "baseAnalyzerId", "models", "processingLocation", "dynamicFieldSchema", "config", "fieldSchema"})
        fields = definition["fieldSchema"]["fields"][evidence_field_name(STEPS[0])]["items"]["properties"]
        self.assertEqual(fields["EstimatedStartMs"]["type"], "integer")
        self.assertEqual(fields["EstimatedEndMs"]["type"], "integer")
        self.assertEqual(fields["EstimatedStartMs"]["method"], "generate")
        self.assertEqual(fields["EstimatedEndMs"]["method"], "generate")
        # The UI's output language is a contract; action wording and observed
        # boundary quality require real video evaluation, not string snapshots.
        self.assertIn("Spanish (es-ES)", fields["Action"]["description"])

    def test_schema_change_requires_new_authorized_hash(self):
        sent = []
        adapter = self.adapter(lambda request: sent.append(request))
        changed = [dict(step) for step in STEPS]
        changed[0]["label"] = "Otra acción"
        with self.assertRaises(AzureError) as error:
            adapter.analyze(self.video(), changed, {"authorization_confirmed": True, "reservation_id": "r1"})
        self.assertEqual(error.exception.code, "schema")
        self.assertEqual(sent, [])

    def test_prior_definition_authorization_cannot_send_current_definition(self):
        # The same vocabulary/model can acquire new extraction semantics.
        # A grant tied to a prior payload must block before token acquisition,
        # even when a reservation is present and the video is locally valid.
        prepared = build_analyzer_definition(STEPS, "gpt-4.1")
        prior_payload = deepcopy(prepared["definition"])
        prior_payload["fieldSchema"]["description"] = "Prior frozen extraction semantics"
        encoded = json.dumps(prior_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        prior_hash = hashlib.sha256(encoded).hexdigest()
        prior_config = AzureConfig(ENDPOINT, TENANT, PRINCIPAL,
                                   "manual_actions_" + prior_hash[:24], "gpt-4.1", prior_hash)
        credential, sent = Credential(), []
        client = httpx.Client(transport=httpx.MockTransport(lambda request: sent.append(request)))
        self.addCleanup(client.close)
        adapter = AzureAdapter(prior_config, client=client, credential=credential,
                               sleep=lambda _: None, authorize=lambda: None)
        with self.assertRaises(AzureError) as error:
            adapter.analyze(self.video(), STEPS, {"authorization_confirmed": True, "reservation_id": "r1"})
        self.assertEqual(error.exception.code, "schema")
        self.assertEqual(sent, [])
        self.assertEqual(credential.scopes, [])

    def test_requires_ledger_reservation_before_token_or_http(self):
        sent, credential = [], Credential()
        adapter = self.adapter(lambda request: sent.append(request), credential)
        with self.assertRaises(AzureError):
            adapter.analyze(self.video(), STEPS, {"authorization_confirmed": True})
        self.assertEqual(sent, [])
        self.assertEqual(credential.scopes, [])

    def test_explicit_principal_mismatch_blocks_upload(self):
        sent = []
        adapter = self.adapter(lambda request: sent.append(request), Credential(principal=TENANT))
        with self.assertRaises(AzureError) as error:
            adapter.analyze(self.video(), STEPS, {"authorization_confirmed": True, "reservation_id": "r1"})
        self.assertEqual(error.exception.code, "identity")
        self.assertEqual(sent, [])

    def test_submit_binary_once_and_poll_with_real_statuses(self):
        sent, progress = [], []
        credential = Credential()

        def handler(request):
            sent.append(request)
            if request.method == "POST":
                self.assertEqual(request.content, b"offline-video-bytes")
                self.assertEqual(request.headers["Content-Type"], "video/mp4")
                self.assertEqual(request.url.params["api-version"], API_VERSION)
                return httpx.Response(202, headers={"Operation-Location": LOCATION})
            if len(sent) == 2:
                return httpx.Response(200, json={"status": "Running"})
            return httpx.Response(200, json=succeeded(), headers={"apim-request-id": "server-456"})

        adapter = self.adapter(handler, credential)
        raw, service = adapter.analyze(self.video(), STEPS, {"authorization_confirmed": True, "reservation_id": "r1"}, lambda state, detail: progress.append((state, detail)))
        self.assertEqual([r.method for r in sent], ["POST", "GET", "GET"])
        self.assertEqual([state for state, _ in progress], ["submitting", "submitted", "processing", "completed"])
        self.assertEqual(raw["status"], "Succeeded")
        self.assertEqual(service["usage"], raw["usage"])
        self.assertEqual(service["request_id"], "server-456")
        self.assertEqual(service["operation_id"], "op-123")
        self.assertEqual(set(credential.scopes), {TOKEN_SCOPE})
        self.assertNotIn(credential.token, json.dumps(service))

    def test_external_operation_location_never_receives_bearer(self):
        sent = []

        def handler(request):
            sent.append(request)
            return httpx.Response(202, headers={"Operation-Location": "https://evil.example/op?api-version=" + API_VERSION})

        with self.assertRaises(AzureError) as error:
            self.adapter(handler).analyze(self.video(), STEPS, {"authorization_confirmed": True, "reservation_id": "r1"})
        self.assertEqual(len(sent), 1)
        self.assertTrue(error.exception.uncertain)
        self.assertNotIn("evil.example", str(error.exception))

    def test_no_redirect_or_submit_retry(self):
        for status in (302, 401, 403, 429, 503):
            sent = []

            def handler(request):
                sent.append(request)
                return httpx.Response(status, headers={"Location": "https://evil.example"})

            with self.subTest(status=status), self.assertRaises(AzureError) as error:
                self.adapter(handler).analyze(self.video(), STEPS, {"authorization_confirmed": True, "reservation_id": "r1"})
            self.assertEqual(len(sent), 1)
            self.assertEqual(error.exception.http_status, status)

    def test_submit_timeout_marks_uncertainty_without_retry(self):
        sent = []

        def handler(request):
            sent.append(request)
            raise httpx.ReadTimeout("private details must not appear", request=request)

        with self.assertRaises(AzureError) as error:
            self.adapter(handler).analyze(self.video(), STEPS, {"authorization_confirmed": True, "reservation_id": "r1"})
        self.assertTrue(error.exception.uncertain)
        self.assertEqual(len(sent), 1)
        self.assertNotIn("private details", str(error.exception))

    def test_poll_recovery_only_gets_existing_operation(self):
        sent = []

        def handler(request):
            sent.append(request)
            return httpx.Response(200, json=succeeded())

        raw, service = self.adapter(handler).poll(LOCATION)
        self.assertEqual([r.method for r in sent], ["GET"])
        self.assertEqual(service["operation_id"], "op-123")
        self.assertEqual(raw["status"], "Succeeded")

    def test_poll_failure_preserves_private_location(self):
        adapter = self.adapter(lambda _: httpx.Response(503, text="secret-token"))
        with self.assertRaises(AzureError) as error:
            adapter.poll(LOCATION)
        self.assertEqual(error.exception.operation_location, LOCATION)
        self.assertTrue(error.exception.uncertain)
        self.assertNotIn("secret-token", str(error.exception))
        self.assertNotIn(LOCATION, str(error.exception))

    def test_poll_rejects_wrong_api_and_analyzer(self):
        for key, value in (("apiVersion", "2026-06-01-preview"), ("analyzerId", "some-other-lab")):
            raw = succeeded()
            raw["result"][key] = value
            with self.subTest(key=key), self.assertRaises(AzureError) as error:
                self.adapter(lambda _: httpx.Response(200, json=raw)).poll(LOCATION)
            self.assertEqual(error.exception.code, "result_contract")

    def test_missing_persistence_stops_after_accepted_post(self):
        sent = []

        def handler(request):
            sent.append(request)
            return httpx.Response(202, headers={"Operation-Location": LOCATION})

        def progress(status, _):
            if status == "submitted":
                raise OSError("disk error")

        with self.assertRaises(AzureError) as error:
            self.adapter(handler).analyze(self.video(), STEPS, {"authorization_confirmed": True, "reservation_id": "r1"}, progress)
        self.assertEqual([r.method for r in sent], ["POST"])
        self.assertEqual(error.exception.operation_location, LOCATION)
        self.assertTrue(error.exception.uncertain)


if __name__ == "__main__":
    unittest.main()
