"""Pinned Content Understanding REST adapter; importing this module never authenticates.

Analyzer preparation is deliberately local. The application validates the human
authorization and reserves its shared ledger before calling ``analyze``.
"""
from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit
import uuid

import httpx

API_VERSION = "2025-11-01"
TOKEN_SCOPE = "https://cognitiveservices.azure.com/.default"
SCHEMA_VERSION = "manual-actions-v1"
Progress = Callable[[str, dict[str, Any]], None]


class AzureError(RuntimeError):
    """Safe user message plus private recovery metadata; never echo server bodies."""

    def __init__(self, code: str, message: str, *, operation_location: str | None = None,
                 uncertain: bool = False, request_id: str | None = None,
                 http_status: int | None = None):
        super().__init__(message)
        self.code = code
        self.operation_location = operation_location
        self.uncertain = uncertain
        self.request_id = request_id
        self.http_status = http_status


def _uuid(value: str, label: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise AzureError("configuration", f"Configura {label} con su UUID explícito.") from None


def validate_endpoint(endpoint: str) -> str:
    """Reject credentials, ports, arbitrary hosts and paths before token acquisition."""
    try:
        url = urlsplit(endpoint)
        host = url.hostname or ""
        allowed = any(host.endswith(suffix) and len(host) > len(suffix)
                      for suffix in (".services.ai.azure.com", ".cognitiveservices.azure.com"))
        valid = (url.scheme == "https" and allowed and not url.username and not url.password
                 and url.port in (None, 443) and url.path in ("", "/")
                 and not url.query and not url.fragment and re.fullmatch(r"[a-z0-9.-]+", host))
    except ValueError:
        valid = False
    if not valid:
        raise AzureError("configuration", "El endpoint debe ser HTTPS y un origen Azure permitido, sin ruta ni credenciales.")
    return f"https://{host}"


def validate_steps(steps: list[dict[str, str]]) -> list[dict[str, str]]:
    if not isinstance(steps, list) or not 3 <= len(steps) <= 6:
        raise AzureError("schema", "Define entre tres y seis pasos.")
    cleaned = []
    for step in steps:
        if not isinstance(step, dict):
            raise AzureError("schema", "Cada paso debe ser un objeto con id y label.")
        sid, label = step.get("id"), step.get("label")
        if (not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", sid)
                or sid == "unknown" or not isinstance(label, str) or not label.strip()
                or len(label) > 240):
            raise AzureError("schema", "Cada paso necesita un ID único y una descripción de hasta 240 caracteres.")
        cleaned.append({"id": sid, "label": label.strip()})
    if len({s["id"] for s in cleaned}) != len(cleaned):
        raise AzureError("schema", "Los IDs de los pasos deben ser únicos.")
    return cleaned


def evidence_field_name(step: dict[str, str]) -> str:
    """Opaque provider key; only the local authorized vocabulary maps it to an ID."""
    candidate = {"id": step["id"], "label": step["label"].strip()}
    encoded = json.dumps(candidate, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return "Evidence_" + hashlib.sha256(encoded).hexdigest()[:24]


def build_analyzer_definition(steps: list[dict[str, str]], model: str,
                              processing_location: str = "geography") -> dict[str, Any]:
    """Unordered vocabulary only: no expected sequence, gold or labeled examples.

    The hash identifies the complete frozen definition, including model selection.
    GA ``models`` takes a model NAME, not an ARM deployment resource ID.
    """
    vocabulary = sorted(validate_steps(steps), key=evidence_field_name)
    if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", model):
        raise AzureError("configuration", "Falta el nombre de modelo autorizado.")
    if processing_location not in ("geography", "dataZone", "global"):
        raise AzureError("configuration", "La ubicación de procesamiento debe fijarse expresamente.")
    definition = {
        "baseAnalyzerId": "prebuilt-video",
        "description": "Manual actions visible in a video; no certification of off-camera execution.",
        "models": {"completion": model},
        "processingLocation": processing_location,
        "dynamicFieldSchema": False,
        "config": {
            "returnDetails": True,
            # GA custom-video extraction follows the tutorial without category
            # routing. enableSegment + a description-only category is rejected
            # with InvalidContentCategoryDefinition: video requires a valid
            # category analyzer/analyzerId. We need no extra subanalyzer here.
        },
        "fieldSchema": {
            "name": SCHEMA_VERSION,
            "description": (
                "Extract manual action occurrences from timestamped visual evidence. Inspect every camera shot independently from beginning to end and compare successive frames within that shot. "
                "A camera cut can jump backward or forward to a different stage; reset contextual assumptions at each cut and never reconstruct a plausible continuous procedure across a cut. "
                "Each Evidence field is an independent unordered candidate action description, not an instruction or an expected sequence. Evaluate every field exhaustively against visible changes and its literal object/location constraints, irrespective of events found for other candidates. "
                "Distinguish an observed transformation from an existing state: a target shape or state already visible when footage starts does not demonstrate that it was formed or prepared in this footage. Every observed assertion must satisfy all explicit candidate constraints, including the objects, relative location, and direction during the transformation. "
                "A short action may be supported by a before/after transition between adjacent sampled frames; it does not need to last several frames. "
                "Do not infer an action from a held pose, mere object proximity, or a completed state first seen after a cut. Return evidence-supported actions and ambiguous visible candidates only; leave missing actions absent."
            ),
            "fields": {
                evidence_field_name(candidate): {
                    "type": "array",
                    "description": (
                        "Evaluate this candidate independently across every camera shot: "
                        + candidate["label"]
                        + " This is an unordered candidate, not an instruction or a required occurrence. "
                        "Return one entry per visible occurrence of this candidate only, or an empty array when there is no evidence of it. "
                        "Inspect all footage specifically for this candidate, including brief actions and actions after a different action has completed; the presence or absence of entries in other fields must not affect this search. "
                        "First identify the candidate-specific visual transition, then locate its onset and completion. For an action that forms or prepares a target shape or state, require a visible change from a different prior configuration into that target within the same camera shot. Holding, moving, or adjusting an already formed target does not demonstrate its creation; neither does knowledge that it must have been created earlier. Its onset is the witnessed manipulation that begins creating the target, excluding earlier retrieval, lifting, straightening, stretching, or repositioning of materials. "
                        "Check every explicit constraint in the candidate description against that transformation, including object identity, relative location, and direction. A transformation at a different location or in a different direction is not the described occurrence. Do not infer a required spatial relation from the action's intended outcome; when it cannot be established visually, mark relevant evidence uncertain. "
                        "End this occurrence before a different candidate's action begins; do not merge that later action into this interval. "
                        "For a candidate involving passing or inserting an object through an opening, require visible crossing of the aperture; approaching, touching, or holding beside the opening is not insertion. "
                        "A brief passing or insertion action can be observed near the beginning of a video or camera shot when successive timestamped frames demonstrate actual crossing; its earlier preparation need not be shown. This does not establish a separate formation or preparation action from the already formed object. A first-frame pose alone is insufficient for any observed action, and an occurrence must not be moved to a later plausible stage. "
                        "Do not infer an occurrence from static poses or continuity across a cut. Return ambiguous relevant evidence as uncertain, without inventing a required observation."
                    ),
                    "items": {"type": "object", "properties": {
                        "SegmentId": {"type": "string", "description": "Existing service segment ID only if that segment's complete temporal bounds coincide with this action's visible onset and completion; empty otherwise. Never invent an ID or substitute a broad scene or whole-clip interval for an action."},
                        "Action": {"type": "string", "method": "generate", "description": "Write in Spanish (es-ES). Describe the visibly different prior configuration, the candidate-specific transformation, and the resulting configuration within this shot, with the relative location and direction that satisfy this field's candidate. For creation or preparation, distinguish the witnessed formation from holding or moving an already formed target. If a prior configuration, transformation, or explicit spatial constraint cannot be seen, state what is uncertain instead of narrating a plausible unseen phase. Do not copy the candidate label as proof, summarize an expected procedure, merge a different action, or infer continuity across a camera cut."},
                        "Objects": {"type": "array", "items": {"type": "string"}, "description": "Objects visibly involved in the action; do not infer hidden objects."},
                        "EvidenceStatus": {"type": "string", "method": "classify", "enum": ["observed", "uncertain"], "enumDescriptions": {"observed": "Successive timestamped frames within one camera shot demonstrate this field's candidate-specific transformation, all explicit object/location/direction constraints, and a valid temporal interval. For creation or preparation, a visibly different prior configuration must change into the target; an existing target held or moved is insufficient. A brief insertion can qualify when the frames show actual aperture crossing. Do not assert exact service grounding.", "uncertain": "There is relevant visual evidence but the candidate-specific transformation, a required object/location/direction constraint, or a valid temporal interval cannot be established. An already formed target, a first-frame pose, generic handling, or a discontinuity across a camera cut alone cannot demonstrate creation or preparation."}},
                        "EstimatedStartMs": {"type": "integer", "method": "generate", "description": "Absolute video time in milliseconds of the first timestamped frame demonstrating this field's candidate-specific transition within the current shot, rather than the first general hand or object movement in that shot. For an action that forms or prepares a target shape or state, start at the manipulation that begins creating the target; exclude earlier retrieval, lifting, straightening, stretching, or repositioning of materials even when they are moving. Review every frame in that shot; do not use the first appearance of the objects, preparatory approach, candidate ID order, or an earlier similar pose. When the onset falls between sampled frames, use the timestamp of the first frame evidencing the candidate-specific transition; do not claim subframe precision. Null if no valid onset estimate is possible, and mark uncertain. This generated estimate is not service grounding."},
                        "EstimatedEndMs": {"type": "integer", "method": "generate", "description": "Absolute video time in milliseconds at the last timestamped frame supporting completion or cessation of this particular candidate action, before later idle holding or a different candidate's action. Evaluate that different action separately in its own field instead of merging it into this interval. Keep the interval within its camera shot. A cut does not demonstrate completion. Do not extend an action to cover a next candidate or all available footage. Null if no valid end estimate is possible, and mark uncertain. This generated estimate is not service grounding."},
                    }},
                }
                for candidate in vocabulary
            },
        },
    }
    encoded = json.dumps(definition, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    return {"analyzer_id": f"manual_actions_{digest[:24]}", "schema_hash": digest, "definition": definition}


@dataclass(frozen=True)
class AzureConfig:
    endpoint: str
    tenant_id: str
    principal_id: str
    analyzer_id: str
    model: str
    schema_hash: str
    processing_location: str = "geography"
    api_version: str = API_VERSION
    poll_interval: float = 2.0
    poll_timeout: float = 600.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "endpoint", validate_endpoint(self.endpoint))
        object.__setattr__(self, "tenant_id", _uuid(self.tenant_id, "tenant_id"))
        object.__setattr__(self, "principal_id", _uuid(self.principal_id, "principal_id"))
        if self.api_version != API_VERSION:
            raise AzureError("configuration", "Esta entrega fija REST 2025-11-01; otra versión exige reconciliar el contrato.")
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", self.analyzer_id):
            raise AzureError("configuration", "ID de analizador inválido.")
        if not re.fullmatch(r"[a-f0-9]{64}", self.schema_hash):
            raise AzureError("configuration", "Falta el SHA-256 completo del esquema congelado.")
        if self.processing_location not in ("geography", "dataZone", "global"):
            raise AzureError("configuration", "Ubicación de procesamiento no admitida.")
        if self.poll_interval < 1 or self.poll_timeout < 1:
            raise AzureError("configuration", "Los intervalos de sondeo deben ser positivos y de al menos un segundo.")


class AzureAdapter:
    def __init__(self, config: AzureConfig, *, credential: Any = None,
                 client: httpx.Client | None = None, sleep: Callable[[float], None] = time.sleep,
                 monotonic: Callable[[], float] = time.monotonic,
                 authorize: Callable[[], None] | None = None):
        self.config = config
        # Delay even constructing the credential until an authorized operation.
        self._credential = credential
        self._owns_client = client is None
        self._client = client or httpx.Client(transport=httpx.HTTPTransport(retries=0),
                                            timeout=60, follow_redirects=False, trust_env=False)
        self._sleep, self._monotonic = sleep, monotonic
        self._authorize = authorize

    def _check_authorization(self, operation_location: str | None = None) -> None:
        try:
            if self._authorize is None:
                raise ValueError("Missing authorization callback")
            self._authorize()
        except Exception:
            raise AzureError("authorization_changed", "La autorización ha cambiado, caducado o no se puede verificar. Detén y reconcilia el alcance antes de continuar.", operation_location=operation_location, uncertain=operation_location is not None) from None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
        if self._credential is not None and hasattr(self._credential, "close"):
            self._credential.close()

    def _token(self) -> str:
        self._check_authorization()
        if self._credential is None:
            mode = os.getenv("MVP_AZURE_CREDENTIAL_MODE", "azure_cli")
            if mode == "managed_identity":
                from azure.identity import ManagedIdentityCredential
                self._credential = ManagedIdentityCredential()
            elif mode == "azure_cli":
                from azure.identity import AzureCliCredential
                self._credential = AzureCliCredential(tenant_id=self.config.tenant_id, process_timeout=10)
            else:
                raise AzureError("identity", "Modo de identidad no autorizado.")
        try:
            token = self._credential.get_token(TOKEN_SCOPE).token
            payload = token.split(".")[1]
            claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
            # A local guard against another cached CLI identity, not JWT verification.
            if (claims.get("tid", "").lower() != self.config.tenant_id
                    or claims.get("oid", "").lower() != self.config.principal_id
                    or str(claims.get("aud", "")).rstrip("/") != TOKEN_SCOPE.removesuffix("/.default")):
                raise ValueError("Identity mismatch")
        except Exception:
            raise AzureError("identity", "La identidad Entra no coincide con tenant/principal autorizados, o falta iniciar sesión explícita.") from None
        return token

    def _operation_url(self, operation_location: str) -> str:
        try:
            url = urlsplit(operation_location)
            expected = urlsplit(self.config.endpoint)
            params = parse_qs(url.query, keep_blank_values=True)
            valid = (url.scheme == expected.scheme and url.hostname == expected.hostname
                     and url.port in (None, 443) and not url.username and not url.password
                     and not url.fragment
                     and re.fullmatch(r"/contentunderstanding/analyzerResults/[A-Za-z0-9._-]{1,128}", url.path)
                     and params == {"api-version": [API_VERSION]})
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise AzureError("operation_location", "Azure devolvió una ubicación de operación fuera del origen o contrato permitido.", uncertain=True)
        return operation_location

    @staticmethod
    def _notify(progress: Progress | None, status: str, detail: dict[str, Any],
                operation_location: str | None = None) -> None:
        if progress:
            try:
                progress(status, detail)
            except Exception:
                raise AzureError("progress_persistence", "No se pudo conservar el estado de la operación. Reanuda por su identificador; no reenvíes el vídeo.", operation_location=operation_location, uncertain=operation_location is not None) from None

    def analyze(self, video: Path, steps: list[dict[str, str]], metadata: dict[str, Any],
                progress: Progress | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        if metadata.get("authorization_confirmed") is not True or not metadata.get("reservation_id"):
            raise AzureError("authorization", "Falta autorización vigente y una reserva del ledger compartido.")
        prepared = build_analyzer_definition(steps, self.config.model, self.config.processing_location)
        if prepared["schema_hash"] != self.config.schema_hash:
            raise AzureError("schema", "La definición no coincide con el esquema autorizado: prepara y autoriza la revisión vigente antes de analizar.")
        video = Path(video)
        if not video.is_file() or not 0 < video.stat().st_size <= 25 * 1024 * 1024:
            raise AzureError("input", "El vídeo debe existir y pesar como máximo 25 MiB.")
        token = self._token()
        request_id = str(uuid.uuid4())
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "video/mp4",
                   "x-ms-client-request-id": request_id}
        self._notify(progress, "submitting", {"request_id": request_id})
        self._check_authorization()
        try:
            # Exactly one POST. No automatic submit retry or alternate endpoint.
            response = self._client.post(
                f"{self.config.endpoint}/contentunderstanding/analyzers/{self.config.analyzer_id}:analyzeBinary",
                params={"api-version": API_VERSION, "processingLocation": self.config.processing_location},
                headers=headers, content=video.read_bytes(), follow_redirects=False)
        except httpx.HTTPError:
            raise AzureError("submit_uncertain", "La conexión falló al enviar. La operación puede haberse aceptado; no reenvíes automáticamente.", uncertain=True, request_id=request_id) from None
        if response.status_code != 202:
            raise AzureError("submit_http", f"Azure no aceptó el análisis (HTTP {response.status_code}). Revisa configuración y presupuesto antes de decidir otro envío.", uncertain=response.status_code >= 500 or response.status_code in (408, 429) or 200 <= response.status_code < 400,
                             request_id=request_id, http_status=response.status_code)
        location = response.headers.get("Operation-Location", "")
        try:
            location = self._operation_url(location)
        except AzureError:
            raise AzureError("operation_location", "El envío se aceptó pero su ubicación no cumple el contrato. Conserva el request ID y no reenvíes.", uncertain=True, request_id=request_id) from None
        op_id = urlsplit(location).path.rsplit("/", 1)[-1]
        self._notify(progress, "submitted", {"operation_id": op_id, "operation_location": location,
                                             "request_id": request_id}, location)
        raw, service = self.poll(location, progress)
        service["client_request_id"] = request_id
        service["reservation_id"] = metadata["reservation_id"]
        return raw, service

    def poll(self, operation_location: str, progress: Progress | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        """Recover an accepted operation after restart, without issuing another POST.

        Caller must recheck authorization for this tenant read. A polling error
        preserves the private operation URL on AzureError for a later resume.
        """
        location = self._operation_url(operation_location)
        op_id = urlsplit(location).path.rsplit("/", 1)[-1]
        started = self._monotonic()
        while self._monotonic() - started < self.config.poll_timeout:
            try:
                self._check_authorization(location)
                token = self._token()
                self._check_authorization(location)
                response = self._client.get(location, headers={"Authorization": f"Bearer {token}"}, follow_redirects=False)
            except AzureError as exc:
                raise AzureError(exc.code, str(exc), operation_location=location, uncertain=True) from None
            except httpx.HTTPError:
                raise AzureError("poll_uncertain", "No se pudo consultar la operación. Reanuda su estado sin volver a enviar el vídeo.", operation_location=location, uncertain=True) from None
            if response.status_code != 200:
                raise AzureError("poll_http", f"No se pudo consultar la operación (HTTP {response.status_code}). Conserva su ID y reanuda el sondeo.", operation_location=location, uncertain=True, http_status=response.status_code)
            try:
                raw = response.json()
                if not isinstance(raw, dict):
                    raise ValueError("Non-object JSON")
                status = str(raw.get("status", "")).lower()
            except (ValueError, TypeError):
                raise AzureError("poll_contract", "La respuesta de estado no cumple el contrato JSON.", operation_location=location, uncertain=True) from None
            if status == "succeeded":
                result = raw.get("result", {})
                if (not isinstance(result, dict) or result.get("apiVersion") != API_VERSION
                        or result.get("analyzerId") != self.config.analyzer_id
                        or not isinstance(result.get("contents"), list)):
                    raise AzureError("result_contract", "La revisión del resultado no coincide con API/analizador configurados.", operation_location=location, uncertain=True)
                self._notify(progress, "completed", {"operation_id": op_id}, location)
                return raw, {"source": "azure", "api_version": result["apiVersion"],
                             "analyzer_id": result["analyzerId"], "operation_id": op_id,
                             "schema_hash": self.config.schema_hash, "model": self.config.model,
                             "model_provenance": "authorized_configuration_not_service_verification",
                             "usage": raw.get("usage", {}), "created_at": result.get("createdAt"),
                             "request_id": response.headers.get("apim-request-id") or response.headers.get("x-ms-request-id")}
            if status in ("failed", "canceled"):
                raise AzureError("operation_failed", "Azure terminó la operación con fallo o cancelación. Su coste puede incluir uso de modelo; revisa el ledger antes de reintentar.", operation_location=location, uncertain=False)
            if status not in ("running", "notstarted"):
                raise AzureError("poll_contract", "Azure devolvió un estado de operación desconocido.", operation_location=location, uncertain=True)
            self._notify(progress, "processing", {"operation_id": op_id, "service_status": raw["status"]}, location)
            delay = self.config.poll_interval
            retry_after = response.headers.get("Retry-After", "")
            if retry_after.isdigit():
                delay = max(delay, min(float(retry_after), 30))
            self._sleep(delay)
        raise AzureError("poll_timeout", "La operación sigue pendiente. Reanuda el sondeo por su ID; no crees un análisis nuevo.", operation_location=location, uncertain=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Preparación local de esquema; no crea recursos ni obtiene tokens.")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare-schema")
    prepare.add_argument("--steps", type=Path, required=True, help="JSON: lista de objetos id,label.")
    prepare.add_argument("--model", required=True, help="Modelo propuesto; no implica permiso ni disponibilidad.")
    prepare.add_argument("--processing-location", choices=("geography", "dataZone", "global"), default="geography")
    args = parser.parse_args()
    steps = json.loads(args.steps.read_text())
    print(json.dumps(build_analyzer_definition(steps, args.model, args.processing_location), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
