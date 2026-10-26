"""Own-subscription azd runtime: configuration and mocked transport only."""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from video_checklist.authorization import AuthorizationError
from video_checklist.config import Settings
from video_checklist.deployment import deployment_configuration
from video_checklist import main
from video_checklist.ledger import Ledger
from video_checklist.azure import AzureError
from tests.test_app import video

STEPS = [{"id": "a", "label": "Abrir el paquete"}, {"id": "b", "label": "Extraer la pieza"}, {"id": "c", "label": "Cerrar el paquete"}]


@pytest.fixture
def configured(tmp_path, monkeypatch):
    values = {"MVP_RUNTIME_DIR": str(tmp_path), "MVP_RUNTIME_PROFILE": "azd", "MVP_MODE": "azure",
              "MVP_AZURE_ENDPOINT": "https://reader-own-account.openai.azure.com",
              "MVP_AZURE_TENANT_ID": "11111111-1111-1111-1111-111111111111",
              "MVP_AZURE_PRINCIPAL_ID": "22222222-2222-2222-2222-222222222222",
              "MVP_AZURE_DEPLOYMENT_NAME": "reader-model", "MVP_AZURE_MODEL": "gpt-6.1-sol",
              "MVP_AZURE_MODEL_VERSION": "2026-09-29", "MVP_AZURE_CREDENTIAL_MODE": "managed_identity",
              "MVP_AZURE_REGION": "swedencentral", "MVP_MAX_ANALYSES": "2", "MVP_MAX_TOTAL_MINUTES": "3"}
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("MVP_AUTHORIZATION_PATH", raising=False)
    return Settings.from_env()


def test_azd_configuration_needs_no_owner_authorization_or_frozen_checkout(configured, monkeypatch):
    monkeypatch.setattr(main, "load_authorization", lambda *_: pytest.fail("Owner authorization must never be loaded"))
    auth = main.cloud_authorization(configured, STEPS)
    assert auth["endpoint"] == "https://reader-own-account.openai.azure.com"
    assert auth["_ledger_path"] == configured.runtime / "ledger.sqlite3"
    assert auth["_budget"] is None  # A local count guard never claims to be a billing cap.
    with TestClient(main.create_app(configured)) as client:
        config = client.get("/api/config").json()
        assert config["mode"] == "azure" and config["cloud_enabled"] and config["editable_steps"]
        assert not config["sample_available"]
        assert client.get("/api/sample").status_code == 404


def test_each_custom_checklist_gets_its_actual_schema_binding(configured):
    first = deployment_configuration(configured, STEPS)
    changed = deployment_configuration(configured, STEPS + [{"id": "d", "label": "Colocar el paquete en la mesa"}])
    assert first["schema_hash"] != changed["schema_hash"]
    assert first["_authorization_hash"] == changed["_authorization_hash"]
    assert main.analysis_binding(first)["schema_hash"] == first["schema_hash"]


def test_operation_limits_survive_reopening_and_block_uncertain_operations(configured):
    auth = deployment_configuration(configured, STEPS)
    ledger = Ledger(auth)
    reservation = ledger.reserve("implementer", "first-job", 2000, "a" * 64)
    with pytest.raises(AuthorizationError, match="activa o incierta"):
        Ledger(auth).reserve("implementer", "second-job", 2000, "b" * 64)
    ledger.update(reservation, "succeeded")
    second = Ledger(auth).reserve("implementer", "second-job", 2000, "b" * 64)
    ledger.update(second, "succeeded")
    with pytest.raises(AuthorizationError, match="agotado"):
        Ledger(auth).reserve("implementer", "third-job", 2000, "c" * 64)


def test_deployment_config_change_blocks_before_provider_submission(configured, monkeypatch):
    auth = deployment_configuration(configured, STEPS)
    adapter = main.make_adapter(auth, "implementer")
    monkeypatch.setenv("MVP_AZURE_DEPLOYMENT_NAME", "changed-model")
    try:
        with pytest.raises(AzureError) as failure:
            adapter._check_authorization()
        assert failure.value.code == "authorization_changed"
    finally:
        adapter.close()


@pytest.mark.parametrize("value", ["fixture", "demo", "synthetic"])
def test_synthetic_modes_are_rejected(value, tmp_path, monkeypatch):
    monkeypatch.setenv("MVP_MODE", value)
    monkeypatch.setenv("MVP_RUNTIME_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="reales"):
        Settings.from_env()


@pytest.mark.parametrize("name,value", [("MVP_AZURE_ENDPOINT", "https://api.openai.com"), ("MVP_MAX_ANALYSES", "0"), ("MVP_MAX_TOTAL_MINUTES", "unlimited"), ("MVP_AZURE_CREDENTIAL_MODE", "azure_cli")])
def test_invalid_or_non_azure_deployment_fails_closed(configured, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(AuthorizationError):
        deployment_configuration(configured, STEPS)


def test_custom_steps_real_route_uses_deployment_binding_and_same_operation(configured, monkeypatch, video):
    # The HTTP route, state and ledger are real; only provider I/O is mocked.
    captured = []
    class Adapter:
        def analyze(self, path, steps, metadata, progress):
            captured.append((steps, metadata))
            (path.parent / "frames.private.json").write_text(json.dumps({"timestamps_ms": [0, 1000]}))
            progress("submitted", {"operation_id": "resp_offline", "operation_location": "https://reader-own-account.openai.azure.com/openai/v1/responses/resp_offline"})
            return {"mock": True}, {"usage": {"total_tokens": 42}}
        def close(self):
            pass
    monkeypatch.setattr(main, "make_adapter", lambda *_: Adapter())
    def normalized(raw, steps, clip, schema, sampling, *, model):
        assert raw == {"mock": True} and sampling["manifest_sha256"]
        assert model == "gpt-6.1-sol"
        return {"source": "azure", "clip": clip, "events": [], "checklist": [{**s, "status": "uncertain", "events": []} for s in steps]}
    monkeypatch.setattr("video_checklist.foundry.normalize_response", normalized)
    headers = {"Idempotency-Key": "own-azure-custom-steps"}
    data = {"steps": json.dumps(STEPS), "license": "My own recording"}
    with TestClient(main.create_app(configured)) as client:
        initial = client.post("/api/jobs", headers=headers, files={"video": ("own.mp4", video, "video/mp4")}, data=data)
        assert initial.status_code == 202
        id_ = initial.json()["id"]
        saved = client.get(f"/api/jobs/{id_}").json()
        assert saved["status"] == "succeeded" and saved["result"]["source"] == "azure"
        assert saved["result"]["analysis_binding"]["schema_hash"] == deployment_configuration(configured, STEPS)["schema_hash"]
        repeated = client.post("/api/jobs", headers=headers, files={"video": ("own.mp4", video, "video/mp4")}, data=data)
        assert repeated.json()["id"] == id_ and len(captured) == 1
        assert captured[0][0] == STEPS and captured[0][1]["reservation_id"]
        assert client.get(f"/api/jobs/{id_}/export.json").status_code == 200


def test_configured_model_and_version_bind_the_adapter_response_and_export(configured, monkeypatch):
    from video_checklist.foundry import build_responses_definition, normalize_response, FoundryConfig
    from video_checklist.azure import evidence_field_name
    from tests.test_foundry import response, SAMPLING, CLIP, LOCATION
    monkeypatch.setenv("MVP_AZURE_MODEL", "gpt-4.1")
    monkeypatch.setenv("MVP_AZURE_MODEL_VERSION", "2025-04-14")
    auth = deployment_configuration(configured, STEPS)
    prepared = main.prepare_definition(auth, STEPS)
    assert prepared["schema_hash"] == auth["schema_hash"]
    assert prepared["definition"]["model"] == "gpt-4.1"
    assert "reasoning" not in prepared["definition"]
    adapter = main.make_adapter(auth, "implementer")
    raw = response(candidates={evidence_field_name(s): {"status": "absent", "occurrences": []} for s in STEPS}, model="gpt-4.1-2025-04-14")
    try:
        assert adapter.config.deployment_bound and adapter.config.model_version == "2025-04-14"
        accepted, metadata = adapter._finish(raw, LOCATION)
        assert metadata["model"] == "gpt-4.1"
        result = normalize_response(accepted, STEPS, CLIP, auth["schema_hash"], SAMPLING, model=auth["model"])
        assert result["model"] == "gpt-4.1"
        with pytest.raises(AzureError, match="modelo"):
            adapter._finish(response(model="different-model"), LOCATION)
    finally:
        adapter.close()
    with pytest.raises(AzureError):
        build_responses_definition(STEPS, "gpt-4.1")  # Frozen legacy experiment stays pinned.
    with pytest.raises(AzureError):
        FoundryConfig(auth["endpoint"], auth["tenant_id"], auth["principal_id"], auth["deployment_name"],
                      "gpt-4.1", auth["schema_hash"], model_version="2025-04-14")


@pytest.mark.parametrize("name", ["MVP_AZURE_MODEL", "MVP_AZURE_MODEL_VERSION"])
def test_empty_model_binding_is_rejected(configured, monkeypatch, name):
    monkeypatch.setenv(name, "")
    with pytest.raises(AuthorizationError, match="modelo"):
        deployment_configuration(configured, STEPS)
