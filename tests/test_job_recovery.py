"""Offline job recovery checks; every Azure request uses MockTransport."""
import base64
import json
from types import SimpleNamespace

import httpx
import pytest

from video_checklist import main
from video_checklist.authorization import AuthorizationError
from video_checklist.azure import API_VERSION, AzureAdapter, AzureConfig, build_analyzer_definition
from video_checklist.config import Settings
from video_checklist.store import Store


STEPS = [{"id": "S1", "label": "Preparar cuerda"}, {"id": "S2", "label": "Pasar por anilla"}, {"id": "S3", "label": "Tensar cuerda"}]
ENDPOINT = "https://offline-lab.services.ai.azure.com"
LOCATION = ENDPOINT + "/contentunderstanding/analyzerResults/op-known?api-version=" + API_VERSION
TENANT = "11111111-1111-1111-1111-111111111111"
PRINCIPAL = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def job_context(tmp_path, monkeypatch):
    settings = Settings("azure", tmp_path, None, "tester")
    store = Store(tmp_path)
    folder = tmp_path / "offline-job"
    folder.mkdir()
    video = folder / "video.mp4"
    video.write_bytes(b"offline-video-bytes")
    doc = {"id": "offline-job", "status": "queued", "input_hash": "offline-input",
           "video_path": str(video), "clip": {"duration_ms": 2000, "hash": "a" * 64},
           "steps": STEPS, "result": None}
    store.create(doc, "offline-request")
    definition = build_analyzer_definition(STEPS, "gpt-4.1-mini")
    auth = {"endpoint": ENDPOINT, "tenant_id": TENANT, "principal_id": PRINCIPAL,
            "analyzer_id": definition["analyzer_id"], "schema_hash": definition["schema_hash"],
            "model": "gpt-4.1-mini", "model_version": "offline", "region": "offline",
            "processing_location": "geography"}
    monkeypatch.setattr(main, "load_authorization", lambda _: auth)
    return SimpleNamespace(settings=settings, store=store, doc=doc, auth=auth)


class Relay:
    def __init__(self, context, fail_at):
        self.context, self.fail_at = context, fail_at
        self.reservations = []
        self.updates = []

    def reserve(self, *args):
        self.reservations.append(args)
        return "known-reservation"

    def update(self, reservation, status, operation_id=None):
        saved = self.context.store.get(self.context.doc["id"])
        self.updates.append((reservation, status, operation_id))
        if status == "active":
            assert saved["operation_location"] == LOCATION
            assert saved["operation_id"] == "op-known"
        if self.fail_at and status in {self.fail_at, "uncertain"}:
            if status == "uncertain":
                assert saved["status"] == "uncertain"
            raise AuthorizationError("Offline relay unavailable")

    def usage(self, *args):
        if self.fail_at == "usage":
            raise AuthorizationError("Offline relay unavailable")


def mock_adapter(monkeypatch, context, terminal_status="Succeeded"):
    requests, clients = [], []
    claims = {"tid": TENANT, "oid": PRINCIPAL, "aud": "https://cognitiveservices.azure.com"}
    encoded = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    credential = SimpleNamespace(get_token=lambda _: SimpleNamespace(token="header." + encoded + ".signature"))

    def handler(request):
        requests.append(request.method)
        if request.method == "POST":
            return httpx.Response(202, headers={"Operation-Location": LOCATION})
        return httpx.Response(200, json={"status": terminal_status, "result": {
            "apiVersion": API_VERSION, "analyzerId": context.auth["analyzer_id"],
            "contents": []}, "usage": {"videoHours": 0.01}})

    def factory(auth, role):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        clients.append(client)
        return AzureAdapter(AzureConfig(
            auth["endpoint"], auth["tenant_id"], auth["principal_id"], auth["analyzer_id"],
            auth["model"], auth["schema_hash"], auth["processing_location"]),
            client=client, credential=credential, authorize=lambda: None)

    monkeypatch.setattr(main, "make_adapter", factory)
    return requests, clients


@pytest.mark.parametrize("fail_at", ["active", "usage", "succeeded"])
def test_relay_failure_preserves_accepted_operation_and_resume_only_polls(job_context, monkeypatch, fail_at):
    context = job_context
    relay = Relay(context, fail_at)
    monkeypatch.setattr(main, "Ledger", lambda _: relay)
    requests, clients = mock_adapter(monkeypatch, context)
    try:
        main.run_job(context.store, context.settings, context.doc["id"])
        saved = context.store.get(context.doc["id"])
        assert saved["status"] == "uncertain"
        assert saved["operation_location"] == LOCATION
        assert saved["operation_id"] == "op-known"
        assert saved["reservation_id"] == "known-reservation"
        assert main.public_job(saved)["can_resume"] is True
        assert "operation_location" not in main.public_job(saved)
        assert saved["result"] is None
        assert requests.count("POST") == 1
        with pytest.raises(ValueError, match="activa o incierta"):
            context.store.create({"id": "new", "input_hash": "new", "status": "queued"}, "new-request")

        relay.fail_at = None
        assert context.store.claim_resume(context.doc["id"]) is True
        main.run_job(context.store, context.settings, context.doc["id"], resume=True)
        assert context.store.get(context.doc["id"])["status"] == "succeeded"
        assert requests.count("POST") == 1
        assert requests[-1] == "GET"
        assert len(relay.reservations) == 1
    finally:
        for client in clients:
            client.close()


@pytest.mark.parametrize("relay_fails", [False, True])
def test_terminal_failure_blocks_new_jobs_until_ledger_reconciles(job_context, monkeypatch, relay_fails):
    context = job_context
    relay = Relay(context, "failed" if relay_fails else None)
    original_update = relay.update

    def update(reservation, status, operation_id=None):
        if status == "failed":
            saved = context.store.get(context.doc["id"])
            assert saved["status"] == "uncertain"
            assert saved["operation_location"] == LOCATION
            with pytest.raises(ValueError, match="activa o incierta"):
                context.store.create({"id": "new", "input_hash": "new", "status": "queued"}, "new-request")
        original_update(reservation, status, operation_id)

    relay.update = update
    monkeypatch.setattr(main, "Ledger", lambda _: relay)
    requests, clients = mock_adapter(monkeypatch, context, "Failed")
    try:
        main.run_job(context.store, context.settings, context.doc["id"])
        saved = context.store.get(context.doc["id"])
        assert saved["status"] == ("uncertain" if relay_fails else "failed")
        assert saved["operation_location"] == LOCATION
        assert requests == ["POST", "GET"]
    finally:
        for client in clients:
            client.close()


def test_changed_checklist_fails_before_ledger_or_adapter(job_context, monkeypatch):
    context = job_context
    changed = [dict(step) for step in STEPS]
    changed[0]["label"] = "Otra acción visible"
    context.store.update(context.doc["id"], steps=changed)
    monkeypatch.setattr(main, "Ledger", lambda _: pytest.fail("No ledger must be constructed for a mismatched schema"))
    monkeypatch.setattr(main, "make_adapter", lambda *_: pytest.fail("No adapter must be constructed for a mismatched schema"))
    main.run_job(context.store, context.settings, context.doc["id"])
    saved = context.store.get(context.doc["id"])
    assert saved["status"] == "failed"
    assert "reservation_id" not in saved
    assert "esquema autorizado" in saved["message"]


def test_resume_authorization_failure_keeps_known_operation_uncertain(job_context, monkeypatch):
    context = job_context
    context.store.update(context.doc["id"], status="processing", reservation_id="known-reservation", operation_location=LOCATION)

    def rejected(_):
        raise AuthorizationError("Offline authorization expired")

    monkeypatch.setattr(main, "load_authorization", rejected)
    monkeypatch.setattr(main, "make_adapter", lambda *_: pytest.fail("Authorization must stop polling"))
    main.run_job(context.store, context.settings, context.doc["id"], resume=True)
    saved = context.store.get(context.doc["id"])
    assert saved["status"] == "uncertain"
    assert main.public_job(saved)["can_resume"] is True
