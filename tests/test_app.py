import json
from pathlib import Path
import subprocess

import pytest
from fastapi.testclient import TestClient

from video_checklist.config import Settings
from video_checklist.main import create_app
from video_checklist import main
from tests.mock_result import mock_result

STEPS = [{"id": "S1", "label": "Preparar cuerda"}, {"id": "S2", "label": "Pasar por anilla"}, {"id": "S3", "label": "Tensar cuerda"}]


@pytest.fixture(scope="session")
def video(tmp_path_factory):
    path = tmp_path_factory.mktemp("media") / "valid.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=160x120:d=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path.read_bytes()


@pytest.fixture
def mock_analysis(monkeypatch):
    # Deliberate offline doubles for HTTP/storage tests, not an application mode.
    monkeypatch.setattr(main, "cloud_authorization", lambda *_: {"provider": "azure_foundry_responses"})
    def complete(store, settings, id_, resume=False):
        doc = store.get(id_)
        result = mock_result(doc["steps"], doc["clip"], lambda *_: None)
        result["source"] = "azure"
        store.update(id_, result=result, status="succeeded", phase="completed", message="Offline test complete")
    monkeypatch.setattr(main, "run_job", complete)


@pytest.fixture
def client(tmp_path, mock_analysis):
    with TestClient(create_app(Settings("azure", tmp_path, None, "implementer"))) as c:
        yield c


def upload(client, video, key="request-test-0001", **extra):
    return client.post("/api/jobs", headers={"Idempotency-Key": key}, files={"video": ("test.mp4", video, "video/mp4")}, data={"steps": json.dumps(STEPS), "license": "CC BY-SA 4.0; example test", **extra})


def test_entire_local_journey_and_idempotency(client, video):
    created = upload(client, video)
    assert created.status_code == 202
    id_ = created.json()["id"]
    job = client.get(f"/api/jobs/{id_}").json()
    assert job["status"] == "succeeded"
    assert job["source"] == "azure"
    assert job["result"]["clip"]["hash"]
    assert "MOCK" in job["result"]["issues"][0]
    assert upload(client, video).json()["id"] == id_
    report = client.get(f"/api/jobs/{id_}/export.json").json()
    html = client.get(f"/api/jobs/{id_}/export.html")
    assert report == job["result"]
    assert report["clip"]["hash"] in html.text and "CC BY-SA" in html.text
    assert client.get(f"/api/jobs/{id_}/video").content == video
    old_events = report["events"]
    review = client.post(f"/api/jobs/{id_}/review", json={"notes": "Revisado manualmente", "decisions": [{"step_id": "S1", "status": "uncertain"}]}).json()
    assert review["result"]["events"] == old_events
    assert review["result"]["review"]["notes"] == "Revisado manualmente"


def test_corrupt_before_processing(client):
    assert upload(client, b"not a video").status_code == 422
    with client.app.state.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_oversize_before_read(client):
    response = client.post("/api/jobs", content=b"", headers={"Content-Length": str(27 * 1024 * 1024)})
    assert response.status_code == 413


def test_invalid_steps_and_idempotency_conflict(client, video):
    assert upload(client, video, steps="[]").status_code == 422
    assert upload(client, video).status_code == 202
    assert upload(client, video, license="other license").status_code == 409


def test_cloud_unconfigured_does_not_reserve_or_call(tmp_path, video, monkeypatch):
    monkeypatch.delenv("MVP_AUTHORIZATION_PATH", raising=False)
    with TestClient(create_app(Settings("azure", tmp_path, None, "implementer"))) as client:
        assert client.get("/api/config").json()["cloud_enabled"] is False
        assert upload(client, video).status_code == 403
        with client.app.state.store.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_reopen_keeps_completed_job(tmp_path, video, mock_analysis):
    settings = Settings("azure", tmp_path, None, "implementer")
    with TestClient(create_app(settings)) as client:
        id_ = upload(client, video).json()["id"]
    with TestClient(create_app(settings)) as client:
        assert client.get(f"/api/jobs/{id_}").json()["status"] == "succeeded"
        assert upload(client, video).json()["id"] == id_


def test_uncertain_operation_blocks_new_submit(client, video):
    doc = {"id": "uncertain-test", "status": "uncertain", "input_hash": "old", "result": None}
    client.app.state.store.create(doc, "old-request-key")
    assert upload(client, video).status_code == 409


def test_cross_origin_and_host_rejected(client):
    assert client.get("/api/config", headers={"Host": "attacker.test"}).status_code == 403
    assert client.post("/api/jobs", headers={"Origin": "https://attacker.test"}).status_code == 403


def test_unknown_review_rejected(client, video):
    id_ = upload(client, video).json()["id"]
    assert client.post(f"/api/jobs/{id_}/review", json={"decisions": [{"step_id": "missing", "status": "observed"}]}).status_code == 422


def test_resume_claim_is_atomic(client):
    from concurrent.futures import ThreadPoolExecutor
    store = client.app.state.store
    store.create({"id": "interrupted", "status": "uncertain", "input_hash": "old", "operation_location": "private-known-operation"}, "known-request")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: store.claim_resume("interrupted"), range(2)))
    assert sorted(results) == [False, True]


def test_lookup_by_key_returns_same_operation(client, video):
    id_ = upload(client, video).json()["id"]
    assert client.get("/api/jobs/by-key/request-test-0001").json()["id"] == id_
    assert client.get("/api/jobs/by-key/no-such-key").status_code == 404


def test_hosted_origin_exact_allowlist(client, monkeypatch):
    monkeypatch.delenv("MVP_HOSTED", raising=False)
    monkeypatch.setenv("MVP_ALLOWED_HOSTS", "specific.azurewebsites.net")
    monkeypatch.setenv("MVP_BASE_URL", "https://specific.azurewebsites.net")
    assert client.get("/health", headers={"Host": "specific.azurewebsites.net", "Origin": "https://specific.azurewebsites.net"}).status_code == 200
    assert client.get("/health", headers={"Host": "another.azurewebsites.net"}).status_code == 403
    assert client.get("/health", headers={"Host": "specific.azurewebsites.net", "Origin": "https://other.test"}).status_code == 403


def test_hosted_internal_health_probe_is_read_only_and_get_only(client, monkeypatch):
    monkeypatch.setenv("MVP_HOSTED", "true")
    monkeypatch.setenv("MVP_ALLOWED_HOSTS", "specific.azurewebsites.net")
    monkeypatch.setenv("MVP_BASE_URL", "https://specific.azurewebsites.net")
    headers = {"Host": "169.254.130.2:8000"}
    response = client.get("/health", headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert set(response.json()) == {"status", "lab_id", "mode", "commit", "expected_commit"}
    assert response.headers["cache-control"] == "no-store"
    assert client.post("/health", headers=headers).status_code == 403
    assert client.get("/health/", headers=headers).status_code == 403
    assert client.get("/api/config", headers={"Host": "specific.azurewebsites.net", "Origin": "https://specific.azurewebsites.net"}).status_code == 200
    assert client.get("/api/config", headers={"Host": "specific.azurewebsites.net", "Origin": "https://external.test"}).status_code == 403
    with client.app.state.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


@pytest.mark.parametrize("path", ["/api/config", "/", "/api/jobs/missing/export.json", "/api/jobs/missing/export.html"])
def test_hosted_internal_probe_host_cannot_access_api_ui_or_exports(client, monkeypatch, path):
    monkeypatch.setenv("MVP_HOSTED", "true")
    assert client.get(path, headers={"Host": "169.254.130.2:8000"}).status_code == 403
    assert client.post("/api/ledger/summary", json={}, headers={"Host": "169.254.130.2:8000"}).status_code == 403


@pytest.mark.parametrize("origin", ["https://external.test", "https://specific.azurewebsites.net", ""])
def test_hosted_internal_health_probe_requires_absent_origin(client, monkeypatch, origin):
    monkeypatch.setenv("MVP_HOSTED", "true")
    monkeypatch.setenv("MVP_BASE_URL", "https://specific.azurewebsites.net")
    assert client.get("/health", headers={"Host": "169.254.130.2:8000", "Origin": origin}).status_code == 403


@pytest.mark.parametrize("hosted", [None, "false", "TRUE"])
def test_local_health_still_rejects_extra_host(client, monkeypatch, hosted):
    if hosted is None:
        monkeypatch.delenv("MVP_HOSTED", raising=False)
    else:
        monkeypatch.setenv("MVP_HOSTED", hosted)
    assert client.get("/health", headers={"Host": "169.254.130.2:8000"}).status_code == 403


def test_ledger_relay_denies_missing_or_wrong_secret(client, monkeypatch):
    monkeypatch.setenv("MVP_LEDGER_RELAY_TOKEN", "a" * 48)
    assert client.post("/api/ledger/summary", json={}).status_code == 403
    assert client.post("/api/ledger/summary", json={}, headers={"X-MVP-Ledger-Token": "b" * 48}).status_code == 403
