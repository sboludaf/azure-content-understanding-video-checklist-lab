"""Configuration for a reader's own azd deployment, without owner-specific files."""
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re

from .authorization import AuthorizationError


def _positive_integer(name, default):
    raw = os.getenv(name, str(default))
    if not re.fullmatch(r"[1-9][0-9]{0,8}", raw):
        raise AuthorizationError(f"{name} debe ser un entero positivo.")
    return int(raw)


def deployment_configuration(settings, steps):
    """Bind a job to its actual deployment and generated schema.

    azd configures resources and managed identity. This is configuration, not a
    fabricated human approval or a promise about future Azure billing.
    """
    from .foundry import MODEL, build_responses_definition, validate_foundry_endpoint
    from .azure import AzureError, _uuid
    if settings.provider not in {"auto", "azure_foundry_responses"}:
        raise AuthorizationError("El despliegue azd usa Azure Foundry Responses.")
    try:
        endpoint = validate_foundry_endpoint(os.getenv("MVP_AZURE_ENDPOINT", ""))
        tenant = _uuid(os.getenv("MVP_AZURE_TENANT_ID", ""), "tenant_id")
        principal = _uuid(os.getenv("MVP_AZURE_PRINCIPAL_ID", ""), "principal_id")
    except AzureError as exc:
        raise AuthorizationError("Azure sin configurar. Despliega la aplicación con azd y sus recursos e identidad administrada.") from exc
    model = os.getenv("MVP_AZURE_MODEL", MODEL)
    version = os.getenv("MVP_AZURE_MODEL_VERSION", "2026-09-29")
    deployment = os.getenv("MVP_AZURE_DEPLOYMENT_NAME", "")
    if (not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", model)
            or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", version)
            or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", deployment)):
        raise AuthorizationError("Configura el deployment, modelo y versión exactos de un modelo Azure con visión y Responses API.")
    if os.getenv("MVP_AZURE_CREDENTIAL_MODE", "managed_identity") != "managed_identity":
        raise AuthorizationError("El despliegue azd usa identidad administrada de Azure.")
    attempts = _positive_integer("MVP_MAX_ANALYSES", 1000)
    minutes = _positive_integer("MVP_MAX_TOTAL_MINUTES", 1500)
    region = os.getenv("MVP_AZURE_REGION", "")
    if not region:
        raise AuthorizationError("Falta la región del recurso Azure.")
    config = {"endpoint": endpoint, "tenant_id": tenant, "principal_id": principal,
              "deployment_name": deployment, "model": model, "model_version": version,
              "region": region, "attempts": attempts, "minutes": minutes,
              "identity_client_id": os.getenv("AZURE_CLIENT_ID", "")}
    digest = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    prepared = build_responses_definition(steps, model, deployment_bound=True)
    return {**config, "provider": "azure_foundry_responses", "api_version": "v1",
            "processing_location": "global", "schema_hash": prepared["schema_hash"],
            "analyzer_id": prepared["analyzer_id"], "_steps": steps,
            "_runtime_profile": "azd", "_authorization_hash": digest,
            "max_attempts": attempts, "max_attempts_per_role": {"implementer": attempts, "tester": attempts},
            "_minutes": Decimal(minutes), "_budget": None, "_bound": Decimal(0),
            "_cost_mode": "azure_subscription", "_ledger_path": settings.runtime / "ledger.sqlite3"}
