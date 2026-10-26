"""No cloud calls: Responses runner binding, canonical reservation, and recovery."""
import base64
from copy import deepcopy
import json
from types import SimpleNamespace

import httpx
import pytest

from video_checklist import main
from video_checklist.config import Settings
from video_checklist.foundry import FoundryAdapter, FoundryConfig, PROVIDER, build_responses_definition
from video_checklist.store import Store
from tests.test_foundry import STEPS, TENANT, PRINCIPAL, ENDPOINT, LOCATION, MODEL, SAMPLING, response


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.setenv("MVP_EXPECTED_COMMIT", "a" * 40)
    settings = Settings("azure", tmp_path, None, "implementer", PROVIDER)
    store = Store(tmp_path)
    folder = tmp_path / "responses-job"
    folder.mkdir()
    video = folder / "video.mp4"
    video.write_bytes(b"offline-video-bytes")
    frames = folder / "frames.private"
    frames.mkdir()
    image = frames / "0001.jpg"
    image.write_bytes(b"offline-image-bytes")
    sampling = SAMPLING | {"frames": [{"filename": image.name, "timestamp_ms": 0}]}
    (folder / "frames.private.json").write_text(json.dumps(sampling))
    monkeypatch.setattr("video_checklist.foundry.sample_frames", lambda *_: sampling)
    doc = {"id": "responses-job", "status": "queued", "input_hash": "offline-input",
           "video_path": str(video), "clip": {"duration_ms": 3000, "hash": "a" * 64},
           "steps": STEPS, "result": None, "analysis_provider": PROVIDER}
    store.create(doc, "offline-request")
    prepared = build_responses_definition(STEPS)
    auth = {"provider": PROVIDER, "api_version": "v1", "endpoint": ENDPOINT,
            "tenant_id": TENANT, "principal_id": PRINCIPAL, "deployment_name": "lab-gpt61",
            "analyzer_id": prepared["analyzer_id"], "schema_hash": prepared["schema_hash"],
            "model": MODEL, "model_version": "2026-09-29", "region": "offline",
            "processing_location": "geography", "resource_id": "offline-resource",
            "_authorization_hash": "offline-auth"}
    monkeypatch.setattr(main, "load_authorization", lambda _: auth)
    return SimpleNamespace(settings=settings, store=store, doc=doc, auth=auth)


@pytest.mark.parametrize("changed", ["schema_hash", "commit", "model_version", "resource_id", "endpoint", "deployment_name"])
def test_stale_response_binding_stops_before_ledger_adapter_or_get(context, monkeypatch, changed):
    historical = main.analysis_binding(context.auth)
    historical[changed] = "historical-different-value"
    context.store.update(context.doc["id"], status="uncertain", reservation_id="old-reservation",
                         operation_location=LOCATION, analysis_binding=historical)
    monkeypatch.setattr(main, "Ledger", lambda *_: pytest.fail("Stale binding must not construct the ledger"))
    monkeypatch.setattr(main, "make_adapter", lambda *_: pytest.fail("Stale binding must not retrieve or submit"))
    main.run_job(context.store, context.settings, context.doc["id"], resume=True)
    saved = context.store.get(context.doc["id"])
    assert saved["status"] == "uncertain" and saved["result"] is None
    assert saved["analysis_binding"] == historical
    assert "otra revisión" in saved["message"]


def test_new_binding_precedes_reservation_and_resume_only_gets_same_response(context, monkeypatch):
    sent, reservations, clients = [], [], []
    updates = []
    expected_binding = main.analysis_binding(context.auth)
    class Ledger:
        def __init__(self, auth):
            pass
        def reserve(self, *args):
            assert context.store.get(context.doc["id"])["analysis_binding"] == expected_binding
            reservations.append(args)
            return "known-reservation"
        def update(self, reservation, status, operation_id=None):
            updates.append(status)
        def usage(self, reservation, usage):
            assert "input_tokens" in usage
    monkeypatch.setattr(main, "Ledger", Ledger)
    fail_poll = [True]
    claims = {"tid": TENANT, "oid": PRINCIPAL, "aud": "https://ai.azure.com"}
    encoded = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    credential = SimpleNamespace(get_token=lambda _: SimpleNamespace(token="header." + encoded + ".signature"))
    def handler(request):
        sent.append(request.method)
        if request.method == "POST":
            return httpx.Response(200, json={"id": "resp_known", "status": "queued"})
        saved = context.store.get(context.doc["id"])
        assert saved["operation_location"] == LOCATION
        assert saved["analysis_binding"] == expected_binding
        return httpx.Response(503) if fail_poll[0] else httpx.Response(200, json=response())
    def factory(auth, role):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        clients.append(client)
        return FoundryAdapter(FoundryConfig(auth["endpoint"], auth["tenant_id"], auth["principal_id"],
                                           auth["deployment_name"], auth["model"], auth["schema_hash"]),
                              client=client, credential=credential, authorize=lambda: None, sleep=lambda _: None)
    monkeypatch.setattr(main, "make_adapter", factory)
    try:
        main.run_job(context.store, context.settings, context.doc["id"])
        saved = context.store.get(context.doc["id"])
        assert saved["status"] == "uncertain" and main.public_job(saved)["can_resume"] is True
        fail_poll[0] = False
        assert context.store.claim_resume(context.doc["id"]) is True
        main.run_job(context.store, context.settings, context.doc["id"], resume=True)
        saved = context.store.get(context.doc["id"])
        assert saved["status"] == "succeeded"
        assert saved["result"]["analysis_provider"] == PROVIDER
        assert saved["result"]["frame_sampling"]["manifest_sha256"]
        assert len(reservations) == 1 and sent == ["POST", "GET", "GET"]
    finally:
        for client in clients:
            client.close()
