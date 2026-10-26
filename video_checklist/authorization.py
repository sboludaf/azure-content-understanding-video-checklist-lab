"""Fail closed: a local app mode or an Azure login is never human consent."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import hashlib
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit

from .config import LAB_ID, PROPOSAL_HASH, ROOT


class AuthorizationError(ValueError):
    pass


def decimal(value):
    try:
        out = Decimal(str(value))
        if not out.is_finite() or out < 0:
            raise InvalidOperation
        return out
    except (InvalidOperation, ValueError):
        raise AuthorizationError("Límite económico inválido") from None


def completion_grant(auth):
    """An adaptive completion order remains finite and tied to this exact account."""
    grant = auth.get("completion_authorization")
    if grant is None:
        return None
    if (not isinstance(grant, dict)
            or grant.get("authorized") is not True
            or grant.get("adaptive_model_architecture_authorized") is not True
            or grant.get("lab_id") != LAB_ID
            or grant.get("proposal_hash") != PROPOSAL_HASH
            or not all(isinstance(grant.get(key), str) and grant[key].strip()
                       for key in ("human_order_reference", "human_question_context", "human_order_literal", "resource_id"))
            or grant["resource_id"] != auth.get("resource_id")
            or grant.get("max_attempts") != 42
            or grant.get("max_attempts_per_role") != {"implementer": 30, "tester": 12}
            or grant.get("max_minutes") != 36
            or grant.get("max_minutes_per_role") != {"implementer": 18, "tester": 18}
            or type(grant.get("concurrency")) is not int or grant["concurrency"] != 1):
        raise AuthorizationError("Completar el laboratorio exige la orden humana explícita, su contexto y límites finitos del mismo recurso.")
    return grant


def analysis_limits(auth):
    """Keep the original allocation unless the separate human grant is recorded.

    All-costs approval and analyzer-definition approval do not increase attempts.
    A short reply such as 'Si' must retain the concrete question it approved.
    Every actor's limit is checked, including when the hosted relay loads the
    Implementer's scope before accepting a Tester reservation.
    """
    ceilings = {"implementer": 12, "tester": 12}
    total_ceiling = 24
    extension = auth.get("analysis_attempt_extension")
    if extension is not None:
        if (not isinstance(extension, dict)
                or extension.get("authorized") is not True
                or extension.get("lab_id") != LAB_ID
                or extension.get("proposal_hash") != PROPOSAL_HASH
                or not all(isinstance(extension.get(key), str) and extension[key].strip()
                           for key in ("human_order_reference", "human_approval_question_literal", "human_order_literal"))
                or extension.get("additional_implementer_attempts") != 3
                or extension.get("max_attempts") != 27
                or extension.get("max_attempts_per_role") != {"implementer": 15, "tester": 12}
                or extension.get("max_minutes") != 36
                or type(extension.get("concurrency")) is not int
                or extension["concurrency"] != 1):
            raise AuthorizationError("La ampliación de análisis exige la orden humana explícita del mismo laboratorio y su pregunta concreta.")
        ceilings = {"implementer": 15, "tester": 12}
        total_ceiling = 27
    completion = completion_grant(auth)
    if completion is not None:
        ceilings = {"implementer": 30, "tester": 12}
        total_ceiling = 42
        if auth.get("max_minutes_per_role") != {"implementer": 18, "tester": 18}:
            raise AuthorizationError("La autorización de finalización conserva 18 minutos para cada actor.")
    elif auth.get("max_minutes_per_role") is not None:
        raise AuthorizationError("Los minutos por actor requieren la autorización explícita de finalización.")
    try:
        attempts = auth["max_attempts"]
        per_role = auth["max_attempts_per_role"]
        minutes = decimal(auth["max_minutes"])
        concurrent = auth["concurrency"]
        valid = (type(attempts) is int and 0 < attempts <= total_ceiling
                 and isinstance(per_role, dict) and set(per_role) == set(ceilings)
                 and all(type(per_role[actor]) is int and 0 < per_role[actor] <= ceiling
                         for actor, ceiling in ceilings.items())
                 and 0 < minutes <= 36
                 and type(concurrent) is int and concurrent == 1)
    except (KeyError, ValueError, TypeError):
        raise AuthorizationError("Faltan límites conjuntos de intentos/minutos/concurrencia.") from None
    if not valid:
        raise AuthorizationError("Los límites exceden la asignación humana de análisis; reconciliar alcance antes de ejecutar.")
    return minutes


def validate_provider_scope(auth):
    provider = auth.get("provider", "azure_content_understanding")
    if provider == "azure_content_understanding":
        if auth.get("api_version") != "2025-11-01":
            raise AuthorizationError("La API de Content Understanding no coincide con el contrato GA.")
        return
    if provider != "azure_foundry_responses" or completion_grant(auth) is None:
        raise AuthorizationError("El proveedor requiere la autorización humana de arquitectura para este laboratorio.")
    data = auth.get("provider_data")
    deployment = auth.get("deployment_name")
    resource_id = auth.get("resource_id", "")
    account = re.fullmatch(r"/subscriptions/[0-9a-fA-F-]{36}/resourceGroups/[^/]+/providers/Microsoft\.CognitiveServices/accounts/([^/]+)", resource_id)
    try:
        url = urlsplit(auth.get("endpoint", ""))
        valid_origin = (account is not None and url.scheme == "https"
                        and url.hostname in {account[1].lower() + ".services.ai.azure.com", account[1].lower() + ".openai.azure.com"}
                        and not url.username and not url.password and url.port in (None, 443)
                        and url.path in ("", "/") and not url.query and not url.fragment)
    except ValueError:
        valid_origin = False
    if (not valid_origin or auth.get("api_version") != "v1"
            or auth.get("model") != "gpt-6.1-sol" or auth.get("model_version") != "2026-09-29"
            or not isinstance(deployment, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", deployment)
            or not isinstance(data, dict) or data.get("verified") is not True
            or data.get("resource_id") != resource_id or data.get("deployment_name") != deployment
            or data.get("deployment_resource_id") != resource_id + "/deployments/" + deployment
            or data.get("deployment_resource_id") not in auth.get("resource_allowlist", [])
            or data.get("model") != auth.get("model") or data.get("model_version") != auth.get("model_version")):
        raise AuthorizationError("Responses exige origen Azure propio y deployment/modelo/version verificados en la allowlist del mismo recurso.")


def load_authorization(role: str):
    raw_path = os.getenv("MVP_AUTHORIZATION_PATH")
    if not raw_path:
        raise AuthorizationError("Azure pendiente: falta autorización humana con recursos y presupuesto para este laboratorio.")
    path = Path(raw_path).expanduser().resolve()
    if not path.is_file() or ROOT in path.parents or "botti-oro" in path.parts:
        raise AuthorizationError("La autorización operativa debe existir fuera del repo y del vault.")
    try:
        raw = path.read_bytes()
        auth = json.loads(raw)
        if not isinstance(auth, dict):
            raise ValueError("Authorization must be an object")
        start = datetime.fromisoformat(auth["valid_from"])
        end = datetime.fromisoformat(auth["valid_until"])
        current = datetime.now(timezone.utc)
        valid_dates = start.tzinfo is not None and end.tzinfo is not None and start <= current < end
    except (ValueError, KeyError, TypeError):
        raise AuthorizationError("Autorización incompleta o vigencia sin zona horaria") from None
    if auth.get("lab_id") != LAB_ID or auth.get("proposal_hash") != PROPOSAL_HASH:
        raise AuthorizationError("La autorización corresponde a otra propuesta o laboratorio.")
    if auth.get("authorized") is not True or auth.get("withdrawn") is not False or not valid_dates:
        raise AuthorizationError("Autorización no concedida, retirada o fuera de vigencia.")
    if not auth.get("human_order_reference") or not auth.get("human_order_literal"):
        raise AuthorizationError("Falta procedencia verificable de la orden humana.")
    roles = auth.get("roles")
    if auth.get("analysis_authorized") is not True or not isinstance(roles, list) or not roles or any(not isinstance(r, str) or r not in {"implementer", "tester"} for r in roles) or role not in roles:
        raise AuthorizationError("La orden no permite análisis para este rol.")
    if auth.get("coexistence_verified") is not True or not auth.get("cleanup_scope") or not auth.get("manifest_path"):
        raise AuthorizationError("Falta reconciliar coexistencia, manifest y alcance de retirada.")
    if auth.get("rbac_verified") is not True or auth.get("mapping_verified") is not True or not auth.get("mapping_evidence"):
        raise AuthorizationError("RBAC y mapping/defaults deben estar verificados explícitamente para el recurso exacto.")
    if auth.get("processing_location") not in {"geography", "dataZone", "global"}:
        raise AuthorizationError("Falta alcance explícito del lugar de procesamiento.")
    expected_commit = os.getenv("MVP_EXPECTED_COMMIT", "")
    try:
        if os.getenv("MVP_HOSTED") == "true":
            manifest = json.loads((ROOT / "source-manifest.json").read_text())
            commit = manifest["commit"]
            dirty = False
        else:
            commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
            dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain"], check=True, capture_output=True, text=True).stdout.strip()
    except (subprocess.SubprocessError, OSError, ValueError, KeyError):
        raise AuthorizationError("No se puede acreditar el commit local congelado.") from None
    if not re.fullmatch(r"[0-9a-f]{40}", expected_commit) or commit != expected_commit or dirty:
        raise AuthorizationError("Azure requiere el SHA completo esperado y un checkout congelado sin cambios.")
    values = {
        "endpoint": "MVP_AZURE_ENDPOINT", "tenant_id": "MVP_AZURE_TENANT_ID",
        "principal_id": "MVP_AZURE_PRINCIPAL_ID", "resource_id": "MVP_AZURE_RESOURCE_ID",
        "analyzer_id": "MVP_AZURE_ANALYZER_ID", "model": "MVP_AZURE_MODEL",
        "schema_hash": "MVP_AZURE_SCHEMA_HASH",
    }
    if auth.get("provider") == "azure_foundry_responses":
        values["deployment_name"] = "MVP_AZURE_DEPLOYMENT_NAME"
    for key, env in values.items():
        if not os.getenv(env) or auth.get(key) != os.getenv(env):
            raise AuthorizationError(f"Configuración {key} no coincide con el alcance humano.")
    if not isinstance(auth.get("resource_allowlist"), list) or auth["resource_id"] not in auth["resource_allowlist"]:
        raise AuthorizationError("El recurso no aparece en la allowlist humana de este laboratorio.")
    validate_provider_scope(auth)
    if not all(auth.get(k) for k in ["model_version", "sku", "region", "rbac_verified", "price_basis", "cost_bound_basis"]):
        raise AuthorizationError("Faltan API/modelo/version/SKU/región/RBAC o base de coste acreditados.")
    all_scoped_costs = auth.get("all_scoped_costs_authorized") is True
    budget = None if all_scoped_costs else decimal(auth.get("budget_eur", 0))
    bound = decimal(auth.get("cost_bound_per_attempt_eur", 0))
    if bound <= 0 or (budget is not None and (budget <= 0 or bound > budget)):
        raise AuthorizationError("Sin presupuesto o cota conservadora de coste por intento.")
    minutes = analysis_limits(auth)
    ledger_path = Path(os.getenv("MVP_LEDGER_PATH", "")).expanduser()
    relay = os.getenv("MVP_LEDGER_URL")
    if relay:
        try:
            url = urlsplit(relay)
            valid_relay = (url.scheme == "https" and url.hostname and not url.username
                           and not url.password and url.port in (None, 443)
                           and not url.query and not url.fragment)
        except ValueError:
            valid_relay = False
        if not valid_relay or relay != auth.get("shared_ledger_url"):
            raise AuthorizationError("El ledger remoto no coincide con el alcance autorizado.")
        # This path identifies the server's canonical ledger. Resolving it on
        # the client can rewrite /home on macOS or follow unrelated local links.
        valid_path = (ledger_path.is_absolute()
                      and str(ledger_path) == auth.get("shared_ledger_path")
                      and ".." not in ledger_path.parts and ROOT not in ledger_path.parents)
    else:
        resolved = ledger_path.resolve()
        valid_path = (ledger_path.is_absolute()
                      and str(resolved) == auth.get("shared_ledger_path")
                      and ROOT not in resolved.parents)
    if not valid_path or "botti-oro" in ledger_path.parts:
        raise AuthorizationError("Falta ledger conjunto explícito fuera del repo y del vault.")
    auth["_budget"] = budget
    auth["_bound"] = bound
    auth["_minutes"] = minutes
    if auth.get("completion_authorization") is not None:
        auth["_minutes_per_role"] = {actor: decimal(value) for actor, value in auth["max_minutes_per_role"].items()}
    auth["_ledger_path"] = ledger_path
    auth["_commit"] = commit
    auth["_authorization_hash"] = hashlib.sha256(raw).hexdigest()
    auth["_cost_mode"] = "scoped_all_costs" if all_scoped_costs else "monetary_cap"
    if relay:
        auth["_ledger_url"] = relay.rstrip("/")
    return auth
