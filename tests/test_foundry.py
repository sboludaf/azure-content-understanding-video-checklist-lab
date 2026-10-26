"""Offline Azure Responses protocol, frame provenance, and uncertainty checks."""
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import httpx
import pytest

from video_checklist.azure import AzureError, evidence_field_name
from video_checklist.foundry import (API_VERSION, MODEL, PROVIDER, TOKEN_SCOPE, FoundryAdapter,
                                     FoundryConfig, build_responses_definition,
                                     normalize_response, sample_frames, validate_foundry_endpoint)

STEPS = [{"id": "S1", "label": "Formar un bucle lejos del aro"},
         {"id": "S2", "label": "Pasar una pieza por la abertura"},
         {"id": "S3", "label": "Tirar del extremo hacia la derecha"}]
TENANT = "11111111-1111-1111-1111-111111111111"
PRINCIPAL = "22222222-2222-2222-2222-222222222222"
ENDPOINT = "https://offline-lab.services.ai.azure.com"
LOCATION = ENDPOINT + "/openai/v1/responses/resp_known"
SAMPLING = {"fps": 2, "max_frames": 49, "max_edge_px": 1280,
            "actual_frame_count": 5, "timestamps_ms": [0, 520, 1000, 1520, 2000]}
CLIP = {"duration_ms": 3000, "hash": "a" * 64}


class Credential:
    def __init__(self, principal=PRINCIPAL, audience="https://ai.azure.com"):
        claims = {"tid": TENANT, "oid": principal, "aud": audience}
        encoded = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
        self.token = "header." + encoded + ".signature"
        self.scopes = []

    def get_token(self, scope):
        self.scopes.append(scope)
        return SimpleNamespace(token=self.token)


def config():
    return FoundryConfig(ENDPOINT, TENANT, PRINCIPAL, "lab-gpt61", MODEL,
                         build_responses_definition(STEPS)["schema_hash"])


def row(**changes):
    value = {"action": "La pieza cruza la abertura", "objects": ["pieza", "aro"],
             "evidence_status": "observed", "start_ms": 1000, "end_ms": 2000,
             "before_ms": 520, "after_ms": 2000, "before_state": "Pieza antes de la abertura",
             "transition": "Se ve el cruce", "after_state": "Pieza al otro lado",
             "constraints": "Cruce visible de la abertura"}
    return value | changes


def response(candidates=None, **changes):
    candidates = candidates or {evidence_field_name(s): {"status": "absent", "occurrences": []} for s in STEPS}
    raw = {"id": "resp_known", "status": "completed", "model": MODEL + "-2026-09-29",
           "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(candidates)}]}],
           "usage": {"input_tokens": 1200, "output_tokens": 500}}
    return raw | changes


@pytest.fixture
def prepared_video(tmp_path, monkeypatch):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"offline-video-bytes")
    frames = tmp_path / "frames.private"
    frames.mkdir()
    image = frames / "0001.jpg"
    image.write_bytes(b"offline-jpeg-bytes")
    sampling = SAMPLING | {"frames": [{"filename": image.name, "timestamp_ms": 0}]}
    monkeypatch.setattr("video_checklist.foundry.sample_frames", lambda *_: sampling)
    return video


def make_adapter(handler, credential=None, **kwargs):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = FoundryAdapter(config(), client=client, credential=credential or Credential(),
                             sleep=lambda _: None, authorize=kwargs.pop("authorize", lambda: None), **kwargs)
    return adapter, client


def test_frozen_definition_is_order_independent_private_and_hashes_all_settings(monkeypatch):
    monkeypatch.setattr(Path, "read_text", lambda *_: pytest.fail("Definition preparation must not read a dataset"))
    first = build_responses_definition(STEPS)
    assert first == build_responses_definition(list(reversed(STEPS)))
    definition = first["definition"]
    assert definition["provider"] == PROVIDER and definition["model"] == MODEL
    assert definition["api_version"] == API_VERSION
    assert definition["sampling"]["requested_fps"] == 2 and definition["sampling"]["max_frames"] == 49
    assert definition["sampling"]["max_content_parts"] == 99
    keys = list(definition["text"]["format"]["schema"]["properties"])
    assert keys == sorted(keys) and set(keys) == {evidence_field_name(s) for s in STEPS}
    body = json.dumps(definition, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert hashlib.sha256(body.encode()).hexdigest() == first["schema_hash"]
    for step in STEPS:
        assert json.dumps(step["id"]) not in body
    assert "gold" not in body and "knowledgeSources" not in body
    changed = deepcopy(STEPS)
    changed[0]["label"] = "Girar una manivela hacia la izquierda"
    assert build_responses_definition(changed)["schema_hash"] != first["schema_hash"]
    with pytest.raises(AzureError):
        build_responses_definition(STEPS, "some-other-model")


def test_task_episode_schema_supports_absence_and_unbounded_separate_repetitions():
    definition = build_responses_definition(STEPS)["definition"]
    fields = definition["text"]["format"]["schema"]["properties"]
    for candidate in fields.values():
        statuses = set(candidate["properties"]["status"]["enum"])
        assert statuses == {"observed", "uncertain", "absent"}
        occurrences = candidate["properties"]["occurrences"]
        assert occurrences["type"] == "array"
        assert "minItems" not in occurrences and "maxItems" not in occurrences
        # Episode granularity is evaluated from visual evidence by the model.
        # The output contract retains all independent returned occurrences;
        # neither schema caps nor local deduplication can force evaluation counts.
        assert occurrences["items"]["type"] == "object"
        assert {"before_state", "transition", "after_state", "constraints"} <= set(occurrences["items"]["required"])


def test_azure_endpoints_only():
    assert validate_foundry_endpoint("https://lab.openai.azure.com/") == "https://lab.openai.azure.com"
    for endpoint in ("https://api.openai.com", "https://lab.services.ai.azure.com.evil.example",
                     "https://user:secret@lab.openai.azure.com", "https://lab.openai.azure.com/openai/v1/",
                     "http://lab.openai.azure.com", "https://lab.openai.azure.com?token=secret"):
        with pytest.raises(AzureError):
            validate_foundry_endpoint(endpoint)


def test_submit_once_private_images_strict_json_and_persist_before_get(prepared_video):
    sent, stages = [], []
    credential = Credential()
    def handler(request):
        sent.append(request.method)
        if request.method == "POST":
            payload = json.loads(request.content)
            assert request.url.path == "/openai/v1/responses"
            assert payload["model"] == "lab-gpt61"
            assert payload["background"] is True and payload["store"] is True
            assert payload["text"]["format"]["strict"] is True
            images = [c for c in payload["input"][0]["content"] if c["type"] == "input_image"]
            assert len(images) == 1 and images[0]["image_url"].startswith("data:image/jpeg;base64,")
            provider_text = {"instructions": payload["instructions"], "text": payload["text"],
                             "input_text": [c for c in payload["input"][0]["content"] if c["type"] == "input_text"]}
            assert json.dumps("S1") not in json.dumps(provider_text)
            return httpx.Response(200, json={"id": "resp_known", "status": "queued"})
        assert any(status == "submitted" and detail["operation_location"] == LOCATION for status, detail in stages)
        return httpx.Response(200, json=response())
    adapter, client = make_adapter(handler, credential)
    try:
        raw, metadata = adapter.analyze(prepared_video, STEPS, {"authorization_confirmed": True, "reservation_id": "r1", "duration_ms": 3000}, lambda state, detail: stages.append((state, detail)))
        assert sent == ["POST", "GET"]
        assert raw["status"] == "completed" and metadata["api_version"] == "v1"
        assert metadata["usage"] == {"input_tokens": 1200, "output_tokens": 500}
        assert set(credential.scopes) == {TOKEN_SCOPE}
        assert credential.token not in json.dumps(metadata)
    finally:
        client.close()


def test_missing_reservation_or_changed_schema_prevents_credentials_and_http(prepared_video):
    sent, credential = [], Credential()
    adapter, client = make_adapter(lambda request: sent.append(request), credential)
    try:
        with pytest.raises(AzureError):
            adapter.analyze(prepared_video, STEPS, {"authorization_confirmed": True, "duration_ms": 3000})
        changed = deepcopy(STEPS)
        changed[1]["label"] = "Otra transformación"
        with pytest.raises(AzureError) as failure:
            adapter.analyze(prepared_video, changed, {"authorization_confirmed": True, "reservation_id": "r1", "duration_ms": 3000})
        assert failure.value.code == "schema"
        assert sent == [] and credential.scopes == []
    finally:
        client.close()


def test_changed_episode_prompt_requires_new_frozen_authorization_before_sampling(prepared_video, monkeypatch):
    from video_checklist import foundry
    current_prompt = foundry.PROMPT
    current = build_responses_definition(STEPS)
    monkeypatch.setattr(foundry, "PROMPT", "Prior frozen task episode boundary semantics")
    prior = build_responses_definition(STEPS)
    monkeypatch.setattr(foundry, "PROMPT", current_prompt)
    assert current["schema_hash"] != prior["schema_hash"]
    prior_definition = deepcopy(prior["definition"])
    prior_definition["instructions"] = current["definition"]["instructions"]
    assert prior_definition == current["definition"]
    monkeypatch.setattr(foundry, "sample_frames", lambda *_: pytest.fail("Stale prompt grant must block before sampling"))
    credential, sent = Credential(), []
    client = httpx.Client(transport=httpx.MockTransport(lambda request: sent.append(request)))
    adapter = FoundryAdapter(FoundryConfig(ENDPOINT, TENANT, PRINCIPAL, "lab-gpt61", MODEL, prior["schema_hash"]),
                             client=client, credential=credential, authorize=lambda: None)
    try:
        with pytest.raises(AzureError) as failure:
            adapter.analyze(prepared_video, STEPS, {"authorization_confirmed": True, "reservation_id": "r1", "duration_ms": 3000})
        assert failure.value.code == "schema"
        assert sent == [] and credential.scopes == []
    finally:
        client.close()


@pytest.mark.parametrize("credential", [Credential(principal=TENANT), Credential(audience="https://cognitiveservices.azure.com")])
def test_wrong_explicit_entra_identity_cannot_send(prepared_video, credential):
    sent = []
    adapter, client = make_adapter(lambda request: sent.append(request), credential)
    try:
        with pytest.raises(AzureError) as failure:
            adapter.analyze(prepared_video, STEPS, {"authorization_confirmed": True, "reservation_id": "r1", "duration_ms": 3000})
        assert failure.value.code == "identity" and sent == []
    finally:
        client.close()


@pytest.mark.parametrize("status", [302, 403, 429, 503])
def test_no_submit_retry_or_redirect(prepared_video, status):
    sent = []
    def handler(request):
        sent.append(request.method)
        return httpx.Response(status, headers={"Location": "https://evil.example"}, text="private-provider-body")
    adapter, client = make_adapter(handler)
    try:
        with pytest.raises(AzureError) as failure:
            adapter.analyze(prepared_video, STEPS, {"authorization_confirmed": True, "reservation_id": "r1", "duration_ms": 3000})
        assert sent == ["POST"]
        assert "private-provider-body" not in str(failure.value)
    finally:
        client.close()


def test_submit_failure_records_only_private_sanitized_provider_body(prepared_video):
    credential = Credential()
    provider_body = json.dumps({"error": {"code": "BadRequest", "message":
        "diagnostic-only; Bearer " + credential.token + "; data:image/jpeg;base64,YWJjZA=="}})
    sent = []
    def handler(request):
        sent.append(request.method)
        return httpx.Response(400, text=provider_body, headers={"x-request-id": "request-private-diagnostic"})
    adapter, client = make_adapter(handler, credential)
    try:
        with pytest.raises(AzureError) as failure:
            adapter.analyze(prepared_video, STEPS, {"authorization_confirmed": True, "reservation_id": "r1", "duration_ms": 3000})
        assert failure.value.http_status == 400 and sent == ["POST"]
        diagnostic = prepared_video.parent / "submit-error.private.json"
        assert diagnostic.stat().st_mode & 0o777 == 0o600
        saved = diagnostic.read_text()
        assert "BadRequest" in saved and "diagnostic-only" in saved
        assert credential.token not in saved and "data:image" not in saved
        assert "offline-jpeg-bytes" not in saved
        assert "diagnostic-only" not in str(failure.value)
    finally:
        client.close()


def test_oversampling_is_rejected_before_token_or_any_http(prepared_video, monkeypatch):
    frames = [{"filename": "0001.jpg", "timestamp_ms": index * 500} for index in range(50)]
    monkeypatch.setattr("video_checklist.foundry.sample_frames", lambda *_: {"frames": frames})
    sent, credential = [], Credential()
    adapter, client = make_adapter(lambda request: sent.append(request), credential)
    try:
        with pytest.raises(AzureError) as failure:
            adapter.analyze(prepared_video, STEPS, {"authorization_confirmed": True, "reservation_id": "r1", "duration_ms": 3000})
        assert failure.value.code == "input"
        assert sent == [] and credential.scopes == []
    finally:
        client.close()


def test_maximum_bound_yields_49_images_and_99_content_parts(prepared_video, monkeypatch):
    frames = [{"filename": "0001.jpg", "timestamp_ms": index * 500} for index in range(49)]
    monkeypatch.setattr("video_checklist.foundry.sample_frames", lambda *_: {"frames": frames})
    sent = []
    def handler(request):
        sent.append(request.method)
        content = json.loads(request.content)["input"][0]["content"]
        assert len(content) == 99
        assert sum(part["type"] == "input_image" for part in content) == 49
        return httpx.Response(200, json=response())
    adapter, client = make_adapter(handler)
    try:
        adapter.analyze(prepared_video, STEPS, {"authorization_confirmed": True, "reservation_id": "r1", "duration_ms": 3000})
        assert sent == ["POST"]
    finally:
        client.close()


def test_accepted_response_persistence_failure_keeps_id_without_poll_or_resubmit(prepared_video):
    sent = []
    def handler(request):
        sent.append(request.method)
        return httpx.Response(200, json={"id": "resp_known", "status": "queued"})
    def progress(state, detail):
        if state == "submitted":
            raise OSError("offline disk unavailable")
    adapter, client = make_adapter(handler)
    try:
        with pytest.raises(AzureError) as failure:
            adapter.analyze(prepared_video, STEPS, {"authorization_confirmed": True, "reservation_id": "r1", "duration_ms": 3000}, progress)
        assert failure.value.uncertain is True and failure.value.operation_location == LOCATION
        assert sent == ["POST"]
    finally:
        client.close()


def test_poll_is_get_only_and_rejects_wrong_model_or_id():
    for bad in (response(model="other-model"), response(id="resp_other")):
        sent = []
        def handler(request):
            sent.append(request.method)
            return httpx.Response(200, json=bad)
        adapter, client = make_adapter(handler)
        try:
            with pytest.raises(AzureError) as failure:
                adapter.poll(LOCATION)
            assert sent == ["GET"] and failure.value.uncertain is True
            assert failure.value.operation_location == LOCATION
        finally:
            client.close()


def test_foreign_response_location_cannot_receive_token():
    sent, credential = [], Credential()
    adapter, client = make_adapter(lambda request: sent.append(request), credential)
    try:
        with pytest.raises(AzureError):
            adapter.poll("https://evil.example/openai/v1/responses/resp_known")
        assert sent == [] and credential.scopes == []
    finally:
        client.close()


def test_normalization_keeps_only_returned_occurrences_and_frame_witnesses():
    candidates = {evidence_field_name(s): {"status": "absent", "occurrences": []} for s in STEPS}
    candidates[evidence_field_name(STEPS[1])] = {"status": "observed", "occurrences": [row()]}
    raw = response(candidates)
    original = deepcopy(raw)
    result = normalize_response(raw, STEPS, CLIP, "schema", SAMPLING)
    assert raw == original
    assert len(result["events"]) == 1
    event = result["events"][0]
    assert event["step_id"] == "S2" and event["time_source"] == "model_estimate"
    assert event["visual_evidence"]["before_ms"] == 520
    assert [r["status"] for r in result["checklist"]] == ["not_observed", "observed", "not_observed"]
    assert result["analysis_provider"] == PROVIDER and result["api_version"] == "v1"


@pytest.mark.parametrize("changes", [{"start_ms": 1234}, {"before_ms": 1000, "start_ms": 520},
                                      {"transition": ""}, {"after_ms": None}, {"evidence_status": "uncertain"}])
def test_unsupported_witness_or_uncertainty_cannot_become_observed(changes):
    candidates = {evidence_field_name(s): {"status": "absent", "occurrences": []} for s in STEPS}
    candidates[evidence_field_name(STEPS[0])] = {"status": "observed", "occurrences": [row(**changes)]}
    result = normalize_response(response(candidates), STEPS, CLIP, "schema", SAMPLING)
    assert len(result["events"]) == 1
    assert result["events"][0]["evidence_status"] == "uncertain"
    assert result["checklist"][0]["status"] == "uncertain"


def test_empty_absence_missing_and_uncertain_fields_never_create_events():
    candidates = {evidence_field_name(STEPS[0]): {"status": "absent", "occurrences": []},
                  evidence_field_name(STEPS[1]): {"status": "uncertain", "occurrences": []}}
    result = normalize_response(response(candidates), STEPS, CLIP, "schema", SAMPLING)
    assert result["events"] == []
    assert [r["status"] for r in result["checklist"]] == ["not_observed", "uncertain", "uncertain"]


def test_witness_alignment_survives_missing_empty_and_malformed_occurrences():
    late = row(start_ms=1520, end_ms=2000, before_ms=1000, after_ms=2000,
               transition="Movimiento posterior de la tercera candidata")
    candidates = {evidence_field_name(STEPS[0]): {"status": "uncertain", "occurrences": [None]},
                  evidence_field_name(STEPS[1]): {"status": "uncertain", "occurrences": []},
                  evidence_field_name(STEPS[2]): {"status": "observed", "occurrences": [row(), late]}}
    result = normalize_response(response(candidates), STEPS, CLIP, "schema", SAMPLING)
    assert [event["id"] for event in result["events"]] == ["event-1-2", "event-1-3"]
    assert all(event["step_id"] == "S3" for event in result["events"])
    assert result["events"][0]["visual_evidence"]["transition"] == "Se ve el cruce"
    assert result["events"][1]["visual_evidence"]["transition"] == late["transition"]
    assert [r["status"] for r in result["checklist"]] == ["uncertain", "uncertain", "observed"]


def test_real_offline_sampler_records_pts_hashes_private_files_and_bounds(tmp_path):
    video = tmp_path / "video.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=160x120:d=2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)], check=True)
    sampled = sample_frames(video, 2000)
    assert 1 <= sampled["actual_frame_count"] <= 5
    assert sampled["timestamps_ms"] == sorted(set(sampled["timestamps_ms"]))
    assert all(0 <= t < 2000 for t in sampled["timestamps_ms"])
    for frame in sampled["frames"]:
        path = tmp_path / "frames.private" / frame["filename"]
        assert path.stat().st_mode & 0o777 == 0o600
        assert hashlib.sha256(path.read_bytes()).hexdigest() == frame["sha256"]
    assert (tmp_path / "frames.private.json").stat().st_mode & 0o777 == 0o600
    assert hashlib.sha256((tmp_path / "frames.private.json").read_bytes()).hexdigest() == sampled["manifest_sha256"]


@pytest.mark.parametrize("duration", [24, 37, 90])
def test_adaptive_sampling_covers_entire_clip_with_bounded_real_frames(tmp_path, duration):
    video = tmp_path / "video.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"color=c=blue:s=160x120:d={duration}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)], check=True)
    sampled = sample_frames(video, duration * 1000)
    assert sampled["actual_frame_count"] <= 49
    assert 1 + 2 * sampled["actual_frame_count"] <= 99
    assert sampled["timestamps_ms"][0] == 0
    assert sampled["timestamps_ms"] == sorted(set(sampled["timestamps_ms"]))
    assert all(0 <= timestamp < duration * 1000 for timestamp in sampled["timestamps_ms"])
    assert sampled["fps"] == min(2, 48 / duration)
    assert sampled["actual_sampling_fps"] == sampled["fps"]
    if duration == 24:
        assert sampled["actual_frame_count"] == 48 and sampled["fps"] == 2
        assert sampled["timestamps_ms"][-1] >= duration * 1000 - 500
    else:
        assert sampled["actual_frame_count"] == 49
        assert sampled["timestamps_ms"][-1] >= duration * 1000 - 40
