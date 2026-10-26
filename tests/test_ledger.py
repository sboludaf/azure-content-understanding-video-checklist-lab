from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import sqlite3
from pathlib import Path

import pytest

from video_checklist.authorization import AuthorizationError, load_authorization
from video_checklist.config import LAB_ID, PROPOSAL_HASH
from video_checklist.ledger import Ledger


def auth(tmp_path):
    return {"_ledger_path": tmp_path / "shared.sqlite3", "_budget": Decimal("5"), "_bound": Decimal("0.20"), "_minutes": Decimal("36"), "max_attempts": 24, "max_attempts_per_role": {"implementer": 12, "tester": 12}}


def test_shared_reservations_concurrency_and_uncertainty(tmp_path):
    a = auth(tmp_path)
    implementer, tester = Ledger(a), Ledger(a)
    first = implementer.reserve("implementer", "job1", 90000)
    with pytest.raises(AuthorizationError):
        tester.reserve("tester", "job2", 90000)
    implementer.update(first, "uncertain")
    with pytest.raises(AuthorizationError):
        tester.reserve("tester", "job2", 90000)
    implementer.update(first, "failed")
    second = tester.reserve("tester", "job2", 90000)
    tester.update(second, "succeeded")
    assert implementer.summary()["attempts_reserved"] == 2
    assert implementer.summary()["cost_reserved_eur"] == "0.40"
    assert implementer.summary()["cost_observed_eur"] is None


def test_role_limit_and_failed_attempts_count(tmp_path):
    ledger = Ledger(auth(tmp_path))
    for i in range(12):
        id_ = ledger.reserve("implementer", str(i), 90000)
        ledger.update(id_, "failed")
    with pytest.raises(AuthorizationError):
        ledger.reserve("implementer", "over", 90000)
    tester_id = ledger.reserve("tester", "tester", 90000)
    assert tester_id


def test_money_limit_never_released_on_failure(tmp_path):
    a = auth(tmp_path)
    a["_budget"] = Decimal("0.20")
    ledger = Ledger(a)
    id_ = ledger.reserve("implementer", "first", 90000)
    ledger.update(id_, "failed")
    with pytest.raises(AuthorizationError):
        ledger.reserve("tester", "second", 90000)


def test_all_scoped_costs_does_not_invent_a_numeric_budget(tmp_path):
    a = auth(tmp_path)
    a["_budget"] = None
    a["_cost_mode"] = "scoped_all_costs"
    ledger = Ledger(a)
    id_ = ledger.reserve("implementer", "first", 90000, "a" * 64)
    assert ledger.reserve("implementer", "first", 90000, "a" * 64) == id_
    assert ledger.summary()["attempts_reserved"] == 1
    assert ledger.summary()["monetary_cap_eur"] is None
    assert ledger.summary()["cost_mode"] == "scoped_all_costs"


def test_foreign_ledger_rejected(tmp_path):
    a = auth(tmp_path)
    with sqlite3.connect(a["_ledger_path"]) as db:
        db.execute("CREATE TABLE identity (lab_id TEXT PRIMARY KEY,proposal_hash TEXT)")
        db.execute("INSERT INTO identity VALUES ('foreign','foreign')")
    with pytest.raises(AuthorizationError):
        Ledger(a)


def test_gate_absent_or_for_other_lab(tmp_path, monkeypatch):
    monkeypatch.delenv("MVP_AUTHORIZATION_PATH", raising=False)
    with pytest.raises(AuthorizationError):
        load_authorization("implementer")
    p = tmp_path / "human.private.json"
    current = datetime.now(timezone.utc)
    p.write_text(json.dumps({"lab_id": "foreign", "proposal_hash": PROPOSAL_HASH, "valid_from": (current-timedelta(hours=1)).isoformat(), "valid_until": (current+timedelta(hours=1)).isoformat()}))
    monkeypatch.setenv("MVP_AUTHORIZATION_PATH", str(p))
    with pytest.raises(AuthorizationError, match="otra propuesta"):
        load_authorization("implementer")


@pytest.mark.parametrize("field", ["coexistence_verified", "rbac_verified", "mapping_verified"])
def test_false_string_never_verifies_cloud_scope(tmp_path, monkeypatch, field):
    current = datetime.now(timezone.utc)
    value = {"lab_id": LAB_ID, "proposal_hash": PROPOSAL_HASH, "valid_from": (current-timedelta(hours=1)).isoformat(), "valid_until": (current+timedelta(hours=1)).isoformat(), "authorized": True, "withdrawn": False, "analysis_authorized": True, "roles": ["implementer", "tester"], "human_order_reference": "offline-test", "human_order_literal": "offline-test", "coexistence_verified": True, "cleanup_scope": "offline-test", "manifest_path": "offline-test", "rbac_verified": True, "mapping_verified": True, "mapping_evidence": "offline-test"}
    value[field] = "false"
    p = tmp_path / "scope.private.json"
    p.write_text(json.dumps(value))
    monkeypatch.setenv("MVP_AUTHORIZATION_PATH", str(p))
    with pytest.raises(AuthorizationError):
        load_authorization("implementer")


def configure_private_scope(tmp_path, monkeypatch, ledger_path, relay=None):
    """Synthetic authorization and source identity; never contacts Azure."""
    from video_checklist import authorization
    root = tmp_path / "source"
    root.mkdir()
    commit = "a" * 40
    (root / "source-manifest.json").write_text(json.dumps({"commit": commit}))
    monkeypatch.setattr(authorization, "ROOT", root)
    monkeypatch.setenv("MVP_HOSTED", "true")
    monkeypatch.setenv("MVP_EXPECTED_COMMIT", commit)
    current = datetime.now(timezone.utc)
    scope = {
        "lab_id": LAB_ID, "proposal_hash": PROPOSAL_HASH,
        "valid_from": (current - timedelta(hours=1)).isoformat(),
        "valid_until": (current + timedelta(hours=1)).isoformat(),
        "authorized": True, "withdrawn": False, "analysis_authorized": True,
        "roles": ["implementer", "tester"], "human_order_reference": "offline-test",
        "human_order_literal": "offline-test", "coexistence_verified": True,
        "cleanup_scope": "offline-test", "manifest_path": "offline-test",
        "rbac_verified": True, "mapping_verified": True, "mapping_evidence": "offline-test",
        "processing_location": "geography", "api_version": "2025-11-01",
        "model_version": "offline", "sku": "S0", "region": "offline",
        "price_basis": "offline-test", "cost_bound_basis": "offline-test",
        "all_scoped_costs_authorized": True, "cost_bound_per_attempt_eur": "0.20",
        "max_attempts": 24, "max_attempts_per_role": {"implementer": 12, "tester": 12},
        "max_minutes": 36, "concurrency": 1, "shared_ledger_path": ledger_path,
    }
    for key, env in {
        "endpoint": "MVP_AZURE_ENDPOINT", "tenant_id": "MVP_AZURE_TENANT_ID",
        "principal_id": "MVP_AZURE_PRINCIPAL_ID", "resource_id": "MVP_AZURE_RESOURCE_ID",
        "analyzer_id": "MVP_AZURE_ANALYZER_ID", "model": "MVP_AZURE_MODEL",
        "schema_hash": "MVP_AZURE_SCHEMA_HASH",
    }.items():
        scope[key] = "offline-" + key
        monkeypatch.setenv(env, scope[key])
    scope["resource_allowlist"] = [scope["resource_id"]]
    monkeypatch.setenv("MVP_LEDGER_PATH", ledger_path)
    if relay is None:
        monkeypatch.delenv("MVP_LEDGER_URL", raising=False)
    else:
        monkeypatch.setenv("MVP_LEDGER_URL", relay)
        scope["shared_ledger_url"] = relay
    auth_path = tmp_path / "private-scope.json"
    auth_path.write_text(json.dumps(scope))
    monkeypatch.setenv("MVP_AUTHORIZATION_PATH", str(auth_path))
    return scope, auth_path


def test_remote_scope_uses_server_path_without_client_resolution(tmp_path, monkeypatch):
    server_path = "/home/mvp-labs/shared.sqlite3"
    relay = "https://hosted-lab.example/api/ledger"
    configure_private_scope(tmp_path, monkeypatch, server_path, relay)
    original_resolve = Path.resolve

    def client_resolve(path, *args, **kwargs):
        if str(path) == server_path:
            pytest.fail("The remote server path must not resolve against the client filesystem")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", client_resolve)
    approved = load_authorization("tester")
    assert str(approved["_ledger_path"]) == server_path
    assert approved["_ledger_url"] == relay
    assert approved["_budget"] is None


@pytest.mark.parametrize("relay", ["http://hosted-lab.example/api/ledger", "https://", "https://user:secret@hosted-lab.example/api/ledger"])
def test_remote_scope_rejects_invalid_https_url(tmp_path, monkeypatch, relay):
    configure_private_scope(tmp_path, monkeypatch, "/home/mvp-labs/shared.sqlite3", relay)
    with pytest.raises(AuthorizationError, match="ledger remoto"):
        load_authorization("tester")


def test_remote_scope_requires_exact_authorized_url_and_server_path(tmp_path, monkeypatch):
    configure_private_scope(tmp_path, monkeypatch, "/home/mvp-labs/shared.sqlite3", "https://hosted-lab.example/api/ledger")
    monkeypatch.setenv("MVP_LEDGER_URL", "https://other-lab.example/api/ledger")
    with pytest.raises(AuthorizationError, match="ledger remoto"):
        load_authorization("tester")
    monkeypatch.setenv("MVP_LEDGER_URL", "https://hosted-lab.example/api/ledger")
    monkeypatch.setenv("MVP_LEDGER_PATH", "/home/mvp-labs/other.sqlite3")
    with pytest.raises(AuthorizationError, match="ledger conjunto"):
        load_authorization("tester")


@pytest.mark.parametrize("path", ["shared.sqlite3", "/home/../home/mvp-labs/shared.sqlite3", "/home/botti-oro/shared.sqlite3"])
def test_remote_scope_rejects_noncanonical_or_vault_path(tmp_path, monkeypatch, path):
    configure_private_scope(tmp_path, monkeypatch, path, "https://hosted-lab.example/api/ledger")
    with pytest.raises(AuthorizationError, match="ledger conjunto"):
        load_authorization("tester")


def test_local_scope_still_requires_resolved_shared_path(tmp_path, monkeypatch):
    server_path = "/home/mvp-labs/shared.sqlite3"
    scope, auth_path = configure_private_scope(tmp_path, monkeypatch, server_path)
    original_resolve = Path.resolve

    def client_resolve(path, *args, **kwargs):
        if str(path) == server_path:
            return Path("/System/Volumes/Data" + server_path)
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", client_resolve)
    with pytest.raises(AuthorizationError, match="ledger conjunto"):
        load_authorization("implementer")
    scope["shared_ledger_path"] = "/System/Volumes/Data" + server_path
    auth_path.write_text(json.dumps(scope))
    assert "_ledger_url" not in load_authorization("implementer")


def record_attempt_extension(scope):
    """Synthetic direct reply with its request, never a real human authorization."""
    scope["max_attempts"] = 27
    scope["max_attempts_per_role"] = {"implementer": 15, "tester": 12}
    scope["analysis_attempt_extension"] = {
        "authorized": True, "lab_id": LAB_ID, "proposal_hash": PROPOSAL_HASH,
        "human_order_reference": "offline-test-reply-to-explicit-extension-request",
        "human_approval_question_literal": "Autorizar 3 análisis adicionales: Implementer15, conjunto27, Tester12,36min,concurrencia1.",
        "human_order_literal": "Si", "additional_implementer_attempts": 3,
        "max_attempts": 27, "max_attempts_per_role": {"implementer": 15, "tester": 12},
        "max_minutes": 36, "concurrency": 1,
    }


def test_all_costs_and_definition_approval_do_not_extend_analysis_attempts(tmp_path, monkeypatch):
    scope, path = configure_private_scope(tmp_path, monkeypatch, str(tmp_path / "shared.sqlite3"))
    scope["analyzer_definition_limit_status"] = "prior_three_limit_expanded_by_direct_human_order"
    assert load_authorization("implementer")["max_attempts"] == 24
    scope.update(max_attempts=27, max_attempts_per_role={"implementer": 15, "tester": 12})
    path.write_text(json.dumps(scope))
    with pytest.raises(AuthorizationError, match="asignación humana"):
        load_authorization("implementer")


@pytest.mark.parametrize("field,value", [
    ("authorized", "true"), ("lab_id", "other-lab"), ("proposal_hash", "b" * 64),
    ("human_order_reference", ""), ("human_approval_question_literal", "   "),
    ("human_order_literal", ""), ("additional_implementer_attempts", 4),
    ("max_attempts", 28), ("max_attempts_per_role", {"implementer": 15, "tester": 11}),
    ("max_minutes", 37), ("concurrency", True),
])
def test_attempt_extension_requires_exact_scoped_human_grant(tmp_path, monkeypatch, field, value):
    scope, path = configure_private_scope(tmp_path, monkeypatch, str(tmp_path / "shared.sqlite3"))
    record_attempt_extension(scope)
    scope["analysis_attempt_extension"][field] = value
    path.write_text(json.dumps(scope))
    with pytest.raises(AuthorizationError, match="orden humana explícita"):
        load_authorization("tester")


@pytest.mark.parametrize("changes", [
    {"max_attempts": 28},
    {"max_attempts_per_role": {"implementer": 16, "tester": 12}},
    {"max_attempts_per_role": {"implementer": 15, "tester": 13}},
    {"max_attempts_per_role": {"implementer": 15.5, "tester": 12}},
    {"max_attempts_per_role": {"tester": 12}},
    {"max_minutes": 37}, {"concurrency": 2},
])
def test_extension_keeps_every_actors_and_joint_ceiling(tmp_path, monkeypatch, changes):
    scope, path = configure_private_scope(tmp_path, monkeypatch, str(tmp_path / "shared.sqlite3"))
    record_attempt_extension(scope)
    scope.update(changes)
    path.write_text(json.dumps(scope))
    # Loading Tester must also reject an invalid Implementer ceiling.
    with pytest.raises(AuthorizationError, match="asignación humana"):
        load_authorization("tester")


def test_explicit_extension_preserves_twelve_historical_attempts_and_tester_allocation(tmp_path, monkeypatch):
    scope, path = configure_private_scope(tmp_path, monkeypatch, str(tmp_path / "shared.sqlite3"))
    original = Ledger(load_authorization("implementer"))
    durations = [37000, 24000, 37000, 37000, 24000, 37000, 37000, 37000, 37000, 37000, 37000, 24000]
    first_id = None
    for i, duration in enumerate(durations):
        reservation = original.reserve("implementer", f"historical-{i}", duration, "a" * 64)
        first_id = first_id or reservation
        original.update(reservation, "failed" if i == 0 else "succeeded")
    before = original.summary()
    assert before["attempts_reserved"] == 12
    assert before["minutes_reserved"] == 6.75
    with pytest.raises(AuthorizationError):
        original.reserve("implementer", "unapproved-thirteenth", 37000)

    record_attempt_extension(scope)
    path.write_text(json.dumps(scope))
    expanded = Ledger(load_authorization("implementer"))
    assert expanded.summary() == before
    assert expanded.reserve("implementer", "historical-0", 37000, "a" * 64) == first_id
    for i, duration in enumerate([37000, 24000, 37000]):
        reservation = expanded.reserve("implementer", f"approved-extra-{i}", duration, "a" * 64)
        expanded.update(reservation, "succeeded")
    with pytest.raises(AuthorizationError):
        expanded.reserve("implementer", "sixteenth", 37000)

    tester = Ledger(load_authorization("tester"))
    for i in range(12):
        reservation = tester.reserve("tester", f"tester-{i}", 90000, "b" * 64)
        tester.update(reservation, "succeeded")
    with pytest.raises(AuthorizationError):
        tester.reserve("tester", "tester-thirteenth", 90000)
    after = tester.summary()
    assert after["attempts_reserved"] == 27
    assert after["states"] == {"reserved": 0, "active": 0, "uncertain": 0, "succeeded": 26, "failed": 1}
    assert after["cost_reserved_eur"] == "5.40"
    assert after["cost_observed_eur"] is None
    assert after["minutes_reserved"] < 36


def record_completion_authorization(scope):
    """Synthetic finite completion order; earlier explicit grant stays historical."""
    record_attempt_extension(scope)
    scope.update(max_attempts=42, max_attempts_per_role={"implementer": 30, "tester": 12},
                 max_minutes_per_role={"implementer": 18, "tester": 18})
    scope["completion_authorization"] = {
        "authorized": True, "adaptive_model_architecture_authorized": True,
        "lab_id": LAB_ID, "proposal_hash": PROPOSAL_HASH,
        "human_order_reference": "offline-direct-completion-order",
        "human_question_context": "Same lab's analyzer, architecture and attempts required for completion.",
        "human_order_literal": "Offline synthetic instruction; not actual approval.",
        "resource_id": scope["resource_id"], "max_attempts": 42,
        "max_attempts_per_role": {"implementer": 30, "tester": 12},
        "max_minutes": 36, "max_minutes_per_role": {"implementer": 18, "tester": 18},
        "concurrency": 1,
    }


def record_responses_scope(scope, monkeypatch):
    resource_id = "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/offline/providers/Microsoft.CognitiveServices/accounts/offline-cu"
    scope.update(provider="azure_foundry_responses", api_version="v1", model="gpt-6.1-sol",
                 model_version="2026-09-29", resource_id=resource_id,
                 endpoint="https://offline-cu.services.ai.azure.com", deployment_name="offline-responses")
    deployment_id = resource_id + "/deployments/offline-responses"
    scope["resource_allowlist"] = [resource_id, deployment_id]
    scope["provider_data"] = {"verified": True, "resource_id": resource_id,
                              "deployment_resource_id": deployment_id, "deployment_name": "offline-responses",
                              "model": "gpt-6.1-sol", "model_version": "2026-09-29"}
    record_completion_authorization(scope)
    for key, env in {"endpoint": "MVP_AZURE_ENDPOINT", "resource_id": "MVP_AZURE_RESOURCE_ID",
                     "model": "MVP_AZURE_MODEL", "deployment_name": "MVP_AZURE_DEPLOYMENT_NAME"}.items():
        monkeypatch.setenv(env, scope[key])


def test_cost_approval_alone_never_enables_completion_limits(tmp_path, monkeypatch):
    scope, path = configure_private_scope(tmp_path, monkeypatch, str(tmp_path / "shared.sqlite3"))
    scope.update(max_attempts=42, max_attempts_per_role={"implementer": 30, "tester": 12})
    path.write_text(json.dumps(scope))
    with pytest.raises(AuthorizationError, match="asignación humana"):
        load_authorization("implementer")


@pytest.mark.parametrize("field,value", [
    ("authorized", "true"), ("adaptive_model_architecture_authorized", False),
    ("lab_id", "foreign"), ("proposal_hash", "b" * 64),
    ("human_order_reference", ""), ("human_question_context", ""), ("human_order_literal", ""),
    ("resource_id", "other-account"), ("max_attempts", 43),
    ("max_attempts_per_role", {"implementer": 31, "tester": 12}),
    ("max_minutes_per_role", {"implementer": 19, "tester": 17}),
])
def test_completion_order_requires_provenance_exact_lab_and_protected_allocation(tmp_path, monkeypatch, field, value):
    scope, path = configure_private_scope(tmp_path, monkeypatch, str(tmp_path / "shared.sqlite3"))
    record_completion_authorization(scope)
    scope["completion_authorization"][field] = value
    path.write_text(json.dumps(scope))
    with pytest.raises(AuthorizationError, match="orden humana"):
        load_authorization("tester")


@pytest.mark.parametrize("changes", [
    {"max_attempts": 43}, {"max_attempts_per_role": {"implementer": 31, "tester": 12}},
    {"max_attempts_per_role": {"implementer": 30, "tester": 13}},
    {"max_minutes": 37}, {"concurrency": 2},
    {"max_minutes_per_role": {"implementer": 19, "tester": 17}},
    {"max_minutes_per_role": None},
])
def test_completion_gate_rejects_overspend_or_borrowed_tester_minutes(tmp_path, monkeypatch, changes):
    scope, path = configure_private_scope(tmp_path, monkeypatch, str(tmp_path / "shared.sqlite3"))
    record_completion_authorization(scope)
    scope.update(changes)
    path.write_text(json.dumps(scope))
    with pytest.raises(AuthorizationError):
        load_authorization("tester")


def test_completion_scope_accepts_responses_only_for_exact_owned_deployment(tmp_path, monkeypatch):
    scope, path = configure_private_scope(tmp_path, monkeypatch, str(tmp_path / "shared.sqlite3"))
    record_responses_scope(scope, monkeypatch)
    path.write_text(json.dumps(scope))
    approved = load_authorization("implementer")
    assert approved["provider"] == "azure_foundry_responses"
    assert approved["_minutes_per_role"] == {"implementer": Decimal(18), "tester": Decimal(18)}
    scope.pop("completion_authorization")
    path.write_text(json.dumps(scope))
    with pytest.raises(AuthorizationError, match="arquitectura"):
        load_authorization("implementer")


@pytest.mark.parametrize("field,value", [
    ("verified", "true"), ("resource_id", "foreign-account"),
    ("deployment_resource_id", "foreign-deployment"), ("deployment_name", "unapproved"),
    ("model", "unapproved-model"), ("model_version", "unapproved-version"),
])
def test_responses_provider_data_cannot_widen_model_or_resource_scope(tmp_path, monkeypatch, field, value):
    scope, path = configure_private_scope(tmp_path, monkeypatch, str(tmp_path / "shared.sqlite3"))
    record_responses_scope(scope, monkeypatch)
    scope["provider_data"][field] = value
    path.write_text(json.dumps(scope))
    with pytest.raises(AuthorizationError, match="Responses exige"):
        load_authorization("implementer")


@pytest.mark.parametrize("endpoint", [
    "https://other-cu.services.ai.azure.com", "https://offline-cu.services.ai.azure.com/openai/v1",
    "https://user:secret@offline-cu.services.ai.azure.com", "https://offline-cu.services.ai.azure.com?token=x",
])
def test_responses_requires_own_https_origin_without_paths_or_credentials(tmp_path, monkeypatch, endpoint):
    scope, path = configure_private_scope(tmp_path, monkeypatch, str(tmp_path / "shared.sqlite3"))
    record_responses_scope(scope, monkeypatch)
    scope["endpoint"] = endpoint
    monkeypatch.setenv("MVP_AZURE_ENDPOINT", endpoint)
    path.write_text(json.dumps(scope))
    with pytest.raises(AuthorizationError, match="Responses exige"):
        load_authorization("implementer")


def test_completion_keeps_fifteen_historical_attempts_and_full_tester_allowance(tmp_path, monkeypatch):
    scope, path = configure_private_scope(tmp_path, monkeypatch, str(tmp_path / "shared.sqlite3"))
    record_attempt_extension(scope)
    path.write_text(json.dumps(scope))
    original = Ledger(load_authorization("implementer"))
    durations = [37000, 24000, 37000, 37000, 24000, 37000, 37000, 37000, 37000, 37000, 37000, 24000, 37000, 24000, 37000]
    for i, duration in enumerate(durations):
        reservation = original.reserve("implementer", f"historical-{i}", duration, "a" * 64)
        original.update(reservation, "failed" if i == 0 else "succeeded")
    before = original.summary()
    record_completion_authorization(scope)
    path.write_text(json.dumps(scope))
    expanded = Ledger(load_authorization("implementer"))
    assert expanded.summary() == before
    for i in range(15):
        reservation = expanded.reserve("implementer", f"new-{i}", 37000, "a" * 64)
        expanded.update(reservation, "succeeded")
    with pytest.raises(AuthorizationError):
        expanded.reserve("implementer", "thirty-first", 24000)
    tester = Ledger(load_authorization("tester"))
    for i in range(12):
        reservation = tester.reserve("tester", f"tester-{i}", 90000, "b" * 64)
        tester.update(reservation, "succeeded")
    summary = tester.summary()
    assert summary["attempts_reserved"] == 42
    assert summary["attempts_per_role"] == {"implementer": 30, "tester": 12}
    assert summary["minutes_reserved_per_role"]["implementer"] < 18
    assert summary["minutes_reserved_per_role"]["tester"] == 18
    assert summary["minutes_reserved"] < 36
    assert summary["states"]["failed"] == 1


def test_completion_role_minutes_stop_implementer_before_using_tester_reserve(tmp_path, monkeypatch):
    scope, path = configure_private_scope(tmp_path, monkeypatch, str(tmp_path / "shared.sqlite3"))
    record_completion_authorization(scope)
    path.write_text(json.dumps(scope))
    ledger = Ledger(load_authorization("implementer"))
    for i in range(12):
        reservation = ledger.reserve("implementer", f"long-{i}", 90000)
        ledger.update(reservation, "failed")
    with pytest.raises(AuthorizationError, match="Minutos de este actor"):
        ledger.reserve("implementer", "next", 1000)
    tester = Ledger(load_authorization("tester"))
    for i in range(12):
        reservation = tester.reserve("tester", f"tester-{i}", 90000)
        tester.update(reservation, "succeeded")
    assert ledger.summary()["minutes_reserved"] == 36
    assert ledger.summary()["minutes_reserved_per_role"] == {"implementer": 18, "tester": 18}
