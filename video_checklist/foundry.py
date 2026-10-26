"""Azure-only Responses analysis of private, timestamped video frames.

Preparation never authenticates. Each clip uses one POST; a persisted response
ID can only be retrieved by GET. Frame witnesses validate temporal references,
not whether a model's interpretation of a visible action is semantically true.
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
import subprocess
import time
from typing import Any
from urllib.parse import urlsplit
import uuid

import httpx

from .azure import AzureAdapter, AzureError, _uuid, evidence_field_name, validate_steps
from .normalizer import normalize as normalize_cu

PROVIDER = "azure_foundry_responses"
API_VERSION = "v1"
MODEL = "gpt-6.1-sol"
TOKEN_SCOPE = "https://ai.azure.com/.default"
SAMPLING = {"fps": 2, "requested_fps": 2, "max_frames": 49, "max_content_parts": 99, "max_edge_px": 1280,
            "timestamp_origin": "decoded_frame_pts", "selection": "first_frame_each_adaptive_bucket",
            "rate_formula": "min(2, (49 - 1) / (duration_ms / 1000))",
            "endpoint_policy": "include_last_decoded_frame_when_adaptive_rate_below_2fps",
            "policy_version": "bounded_full_clip_v1"}
PROMPT = (
    "Review the timestamped frames of one video. The candidate descriptions are an unordered set, not a script or an expected sequence. "
    "Inspect every candidate independently across all frames. A cut can jump between stages; never reconstruct continuity across it. "
    "Report only actions demonstrated by visible changes. Holding or moving an already formed object does not establish that its shape was created in this video. "
    "For formation or creation, require a visible change from a different prior configuration into the target shape or state. For preparation or setup, the transformation may instead deploy, size, unfold, align, or arrange existing material into the described ready configuration; it need not create the material or shape from nothing. Preparation still requires a task-specific visible change, not the mere presence of a ready object or its transfer into a later task. "
    "Respect every literal participant, object, location, and direction constraint in the candidate. Proximity or a plausible intended outcome does not satisfy an unseen constraint. The episode starts only when the described action is visibly performed with its specified participants and location; a later completed result cannot turn earlier offscreen handling or material retrieval into that action. A before witness may show the prior state, but the start must demonstrate the candidate-specific action and constraints. "
    "Insertion or passing through an opening requires visible aperture crossing, not approach. At least one supplied frame must actively demonstrate material crossing the aperture or its visible passage trajectory through it. Outside-before and already-inserted-after states alone cannot prove an insertion hidden by a cut, abrupt discontinuity, or occlusion. If the crossing itself is not visibly supported, preserve uncertainty instead of reconstructing it. A short crossing may be underway at the first frame if visible evidence demonstrates its passage; this does not establish an earlier preparation action. "
    "The unit of an occurrence is the whole task described by the candidate and its completed functional outcome, not each hand movement, intermediate configuration, or adjustment. "
    "When the same workpiece remains in one ongoing unfinished task, aggregate its connected manipulations, regrips, pauses, partial constructions, and adjustments into one task episode. Start at the first manipulation that performs the described task and end when its complete result is visibly established and stable. "
    "Locate the start of a formation or preparation episode retrospectively from its visible stable result: trace the same workpiece backward through the continuous causal chain to the earliest visible manipulation that performs the described task with its specified participants and location. Include preparatory unfolding or drawing out the material, gathering, alignment, and regrips only while those literal constraints and the task-specific causal action are visibly demonstrated; do not start only at the later moment when the final shape becomes obvious. A plausible link to later preparation is insufficient to include retrieval or handling before that visible constrained action begins. "
    "Stop that backward trace at a cut, a genuine reset, an unrelated activity, or a distinct already completed task. Do not include a different candidate's completed episode as preparation. This does not replace a candidate-specific onset: for insertion, the observed onset remains aperture crossing rather than approach. A target already formed in the first available frame still cannot establish a creation episode without a visibly demonstrated new formation. "
    "A nested suboperation or temporary tightening within that unfinished compound task is not an independently completed occurrence of another candidate merely because its motion resembles one. Report that other candidate only when its own full described goal is independently accomplished, including any required resulting state. "
    "An intermediate result that is immediately manipulated further as part of completing the same task does not finish an episode. A brief pause, repeated stroke, or repositioning alone does not reset it. "
    "A separate occurrence requires a visible reset to a fresh pre-task state, a fresh workpiece, or a clearly restarted independent whole task, followed by a fresh completion of the full described goal. Preserve every genuine separate completed repetition; there is no one-per-candidate limit. "
    "Do not merge independent completed episodes, invent a missing reset, or extend an episode over a later independent task. Describe the whole episode's before state, connected transformation, and stable completed result in its visual witnesses. "
    "For each occurrence cite two different supplied frame timestamps as before/after witnesses and describe their visible change and constraints in Spanish. "
    "Use only supplied timestamps for start/end/before/after, with before <= start < end <= after. These are model estimates from sampled frames, not independent semantic grounding. "
    "Candidate status observed requires at least one supported occurrence. Absent means this video provides no evidence of the candidate: return no occurrences. "
    "Uncertain means relevant evidence cannot establish the transformation or constraints; preserve uncertainty instead of filling an expected phase. "
    "A static first-frame state alone never proves its creation. Do not infer any action from an expected procedure, missing footage, or candidate position."
)


def build_responses_definition(steps: list[dict[str, str]], model: str = MODEL, *, deployment_bound: bool = False) -> dict[str, Any]:
    """Freeze provider, exact model, sampling, prompt, and strict output schema."""
    if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", model):
        raise AzureError("configuration", "Configura un nombre de modelo Azure válido.")
    if not deployment_bound and model != MODEL:
        raise AzureError("configuration", "Esta ruta fija el modelo solicitado gpt-6.1-sol.")
    candidates = sorted(validate_steps(steps), key=evidence_field_name)
    occurrence_properties = {
        "action": {"type": "string"}, "objects": {"type": "array", "items": {"type": "string"}},
        "evidence_status": {"type": "string", "enum": ["observed", "uncertain"]},
        **{key: {"type": ["integer", "null"]} for key in ("start_ms", "end_ms", "before_ms", "after_ms")},
        **{key: {"type": "string"} for key in ("before_state", "transition", "after_state", "constraints")},
    }
    fields = {
        evidence_field_name(candidate): {
            "type": "object", "description": candidate["label"],
            "properties": {
                "status": {"type": "string", "enum": ["observed", "uncertain", "absent"]},
                "occurrences": {"type": "array", "items": {
                    "type": "object", "properties": occurrence_properties,
                    "required": list(occurrence_properties), "additionalProperties": False}},
            }, "required": ["status", "occurrences"], "additionalProperties": False,
        } for candidate in candidates
    }
    definition = {
        "provider": PROVIDER, "api_version": API_VERSION, "model": model,
        "sampling": dict(SAMPLING), "instructions": PROMPT,
        "max_output_tokens": 16000,
        "background": True, "store": True,
        "text": {"format": {"type": "json_schema", "name": "manual_actions_frames_v1", "strict": True,
                 "schema": {"type": "object", "properties": fields,
                            "required": list(fields), "additionalProperties": False}}},
    }
    # Readers may select a compatible non-reasoning vision model. Its default
    # behavior is preferable to sending an unsupported reasoning parameter.
    # Preserve the frozen experiment's exact request contract in legacy mode.
    if not deployment_bound:
        definition["reasoning"] = {"effort": "high"}
    digest = hashlib.sha256(json.dumps(definition, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    return {"analyzer_id": "frame_actions_" + digest[:24], "schema_hash": digest, "definition": definition}


def validate_foundry_endpoint(endpoint: str) -> str:
    try:
        url = urlsplit(endpoint)
        host = url.hostname or ""
        allowed = any(host.endswith(suffix) and len(host) > len(suffix)
                      for suffix in (".services.ai.azure.com", ".openai.azure.com"))
        valid = (url.scheme == "https" and allowed and not url.username and not url.password
                 and url.port in (None, 443) and url.path in ("", "/") and not url.query
                 and not url.fragment and re.fullmatch(r"[a-z0-9.-]+", host))
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise AzureError("configuration", "Configura el origen HTTPS de Azure Foundry autorizado, sin ruta ni credenciales.")
    return "https://" + host


@dataclass(frozen=True)
class FoundryConfig:
    endpoint: str
    tenant_id: str
    principal_id: str
    deployment_name: str
    model: str
    schema_hash: str
    model_version: str = "2026-09-29"
    processing_location: str = "geography"
    api_version: str = API_VERSION
    poll_interval: float = 2.0
    poll_timeout: float = 600.0
    deployment_bound: bool = False

    def __post_init__(self):
        object.__setattr__(self, "endpoint", validate_foundry_endpoint(self.endpoint))
        object.__setattr__(self, "tenant_id", _uuid(self.tenant_id, "tenant_id"))
        object.__setattr__(self, "principal_id", _uuid(self.principal_id, "principal_id"))
        model_valid = isinstance(self.model, str) and bool(re.fullmatch(r"[A-Za-z0-9._-]{1,128}", self.model))
        version_valid = isinstance(self.model_version, str) and bool(re.fullmatch(r"[A-Za-z0-9._-]{1,128}", self.model_version))
        legacy_valid = self.deployment_bound or (self.model == MODEL and self.model_version == "2026-09-29")
        if (not model_valid or not version_valid or not legacy_valid or self.api_version != API_VERSION
                or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", self.deployment_name)
                or not re.fullmatch(r"[a-f0-9]{64}", self.schema_hash)
                or self.poll_interval < 1 or self.poll_timeout < 1):
            raise AzureError("configuration", "Falta el binding de modelo/deployment y esquema Azure Foundry autorizado.")


def sample_frames(video: Path, duration_ms: int) -> dict[str, Any]:
    """Decode at most 49 frames across the complete clip, with real PTS."""
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, int) or not 0 < duration_ms <= 90000:
        raise AzureError("input", "El vídeo debe durar como máximo 90 segundos.")
    directory = video.parent / "frames.private"
    directory.mkdir(mode=0o700, exist_ok=True)
    if any(directory.iterdir()):
        raise AzureError("input", "Hay fotogramas anteriores en este job; reconcilia el estado antes de otro envío.")
    rate = min(2.0, (SAMPLING["max_frames"] - 1) / (duration_ms / 1000))
    try:
        count = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
                                "-show_entries", "stream=nb_read_frames", "-of", "json", str(video)],
                               capture_output=True, timeout=60)
        frame_count = int(json.loads(count.stdout)["streams"][0]["nb_read_frames"])
        if count.returncode or frame_count < 1:
            raise ValueError("No decoded frame count")
        last_frame = f"eq(n,{frame_count - 1})+" if rate < 2 else ""
        filter_ = (f"select='{last_frame}isnan(prev_selected_t)+gte(t,(floor(prev_selected_t*{rate!r})+1)/{rate!r})',"
                   "scale='min(1280,iw)':'min(1280,ih)':force_original_aspect_ratio=decrease,showinfo")
        process = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "info", "-xerror", "-i", str(video),
                                  "-map", "0:v:0", "-an", "-vf", filter_, "-fps_mode", "vfr",
                                  "-frames:v", "50", "-q:v", "3", str(directory / "%04d.jpg")],
                                 capture_output=True, timeout=90)
        paths = sorted(directory.glob("*.jpg"))
        timestamps = [round(float(value) * 1000) for value in re.findall(
            rb"pts_time:([0-9.eE+-]+)", process.stderr)]
        if (process.returncode or not paths or len(paths) > SAMPLING["max_frames"]
                or len(paths) != len(timestamps) or len(set(timestamps)) != len(timestamps)
                or timestamps != sorted(timestamps) or not all(0 <= t < duration_ms for t in timestamps)):
            raise ValueError("Invalid decoded frame manifest")
    except (FileNotFoundError, ValueError, KeyError, IndexError, subprocess.TimeoutExpired):
        raise AzureError("input", "No se pudieron muestrear fotogramas verificables. Revisa ffmpeg y el MP4 antes de enviarlo.") from None
    frames = []
    for path, timestamp in zip(paths, timestamps):
        path.chmod(0o600)
        frames.append({"filename": path.name, "timestamp_ms": timestamp,
                       "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    sampling = {**SAMPLING, "fps": rate, "actual_sampling_fps": rate,
                "actual_frame_count": len(frames), "timestamps_ms": timestamps, "frames": frames}
    manifest = video.parent / "frames.private.json"
    manifest.write_text(json.dumps(sampling, ensure_ascii=False) + "\n")
    manifest.chmod(0o600)
    return sampling | {"manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest()}


class FoundryAdapter(AzureAdapter):
    """One Azure Responses POST with explicit Entra identity and durable GET recovery."""
    def _token(self) -> str:
        self._check_authorization()
        if self._credential is None:
            mode = os.getenv("MVP_AZURE_CREDENTIAL_MODE", "managed_identity")
            if mode == "managed_identity":
                from azure.identity import ManagedIdentityCredential
                client_id = os.getenv("AZURE_CLIENT_ID")
                self._credential = ManagedIdentityCredential(**({"client_id": client_id} if client_id else {}))
            elif mode == "azure_cli":
                from azure.identity import AzureCliCredential
                self._credential = AzureCliCredential(tenant_id=self.config.tenant_id, process_timeout=10)
            else:
                raise AzureError("identity", "Modo de identidad no autorizado.")
        try:
            token = self._credential.get_token(TOKEN_SCOPE).token
            payload = token.split(".")[1]
            claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
            if (claims.get("tid", "").lower() != self.config.tenant_id
                    or claims.get("oid", "").lower() != self.config.principal_id
                    or str(claims.get("aud", "")).rstrip("/") != "https://ai.azure.com"):
                raise ValueError("Identity mismatch")
        except Exception:
            raise AzureError("identity", "La identidad Entra no coincide con tenant/principal y audiencia Azure Foundry autorizados.") from None
        return token

    def _operation_url(self, operation_location: str) -> str:
        try:
            url, expected = urlsplit(operation_location), urlsplit(self.config.endpoint)
            valid = (url.scheme == expected.scheme and url.hostname == expected.hostname and url.port in (None, 443)
                     and not url.username and not url.password and not url.query and not url.fragment
                     and re.fullmatch(r"/openai/v1/responses/resp_[A-Za-z0-9_-]{1,160}", url.path))
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise AzureError("operation_location", "El identificador de respuesta no pertenece al endpoint Azure autorizado.")
        return operation_location

    def _finish(self, raw: dict, location: str) -> tuple[dict, dict]:
        expected = {self.config.model, self.config.model + "-" + self.config.model_version, self.config.deployment_name}
        if raw.get("model") not in expected:
            raise AzureError("result_contract", "La respuesta no acredita el modelo autorizado.", operation_location=location, uncertain=True)
        usage = raw.get("usage", {})
        safe_usage = {}
        if isinstance(usage, dict):
            for key in ("input_tokens", "output_tokens", "total_tokens"):
                value = usage.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    safe_usage[key] = value
            for key, detail in (("input_tokens_details", "cached_tokens"), ("output_tokens_details", "reasoning_tokens")):
                value = usage.get(key, {}).get(detail) if isinstance(usage.get(key), dict) else None
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    safe_usage[key] = {detail: value}
        return raw, {"api_version": API_VERSION, "model": self.config.model,
                     "model_provenance": "authorized_deployment_binding_and_response_model",
                     "operation_id": raw["id"], "processing_location": self.config.processing_location,
                     "usage": safe_usage, "analysis_provider": PROVIDER}

    @staticmethod
    def _persist_submit_error(video: Path, response: httpx.Response, token: str) -> None:
        """Private provider response only; never persist request headers/images."""
        body = response.text[:262144].replace(token, "[redacted-token]")
        body = re.sub(r"(?i)Bearer\s+[A-Za-z0-9._~+/-]+", "Bearer [redacted-token]", body)
        body = re.sub(r"data:image/[^;,\s]+;base64,[A-Za-z0-9+/=]+", "[redacted-image]", body)
        diagnostic = {"http_status": response.status_code, "response_body_sanitized": body,
                      "provider_request_id": response.headers.get("x-request-id") or response.headers.get("x-ms-request-id"),
                      "request_payload_saved": False}
        path = video.parent / "submit-error.private.json"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.chmod(path, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(json.dumps(diagnostic, ensure_ascii=False) + "\n")

    def analyze(self, video: Path, steps: list[dict[str, str]], metadata: dict[str, Any], progress=None):
        if metadata.get("authorization_confirmed") is not True or not metadata.get("reservation_id"):
            raise AzureError("authorization", "Falta autorización vigente y reserva del ledger conjunto.")
        prepared = build_responses_definition(steps, self.config.model, deployment_bound=self.config.deployment_bound)
        if prepared["schema_hash"] != self.config.schema_hash:
            raise AzureError("schema", "La definición de fotogramas no coincide con el esquema autorizado.")
        video = Path(video)
        if not video.is_file() or not 0 < video.stat().st_size <= 25 * 1024 * 1024:
            raise AzureError("input", "El vídeo debe existir y pesar como máximo 25 MiB.")
        self._check_authorization()
        self._notify(progress, "sampling", {"message": "Preparando fotogramas privados con sus tiempos reales."})
        sampling = sample_frames(video, metadata.get("duration_ms"))
        if (not isinstance(sampling.get("frames"), list) or not 1 <= len(sampling["frames"]) <= SAMPLING["max_frames"]
                or 1 + 2 * len(sampling["frames"]) > SAMPLING["max_content_parts"]):
            raise AzureError("input", "El muestreo supera el límite operativo de fotogramas. No se envía ninguna petición.")
        content = [{"type": "input_text", "text": "Video duration_ms: " + str(metadata["duration_ms"])}]
        for frame in sampling["frames"]:
            image_path = video.parent / "frames.private" / frame["filename"]
            content.extend([{"type": "input_text", "text": "Frame timestamp_ms: " + str(frame["timestamp_ms"])},
                            {"type": "input_image", "detail": "high", "image_url": "data:image/jpeg;base64," + base64.b64encode(image_path.read_bytes()).decode()}])
        frozen = prepared["definition"]
        body = {key: frozen[key] for key in ("instructions", "reasoning", "max_output_tokens", "background", "store", "text") if key in frozen}
        body.update(model=self.config.deployment_name, input=[{"role": "user", "content": content}])
        token = self._token()
        request_id = str(uuid.uuid4())
        self._notify(progress, "submitting", {"request_id": request_id, "message": "Enviando una sola petición privada a Azure Foundry."})
        self._check_authorization()
        try:
            response = self._client.post(self.config.endpoint + "/openai/v1/responses", json=body,
                headers={"Authorization": "Bearer " + token, "Content-Type": "application/json", "x-ms-client-request-id": request_id},
                follow_redirects=False)
        except httpx.HTTPError:
            raise AzureError("submit_uncertain", "La conexión falló durante el envío; puede haberse aceptado. Conserva la reserva y no reenvíes automáticamente.", uncertain=True, request_id=request_id) from None
        if response.status_code not in (200, 201, 202):
            self._persist_submit_error(video, response, token)
            raise AzureError("submit_http", f"Azure Foundry no aceptó la petición (HTTP {response.status_code}).", uncertain=response.status_code >= 500 or response.status_code in (408, 429), http_status=response.status_code, request_id=request_id)
        try:
            raw = response.json()
            location = self._operation_url(self.config.endpoint + "/openai/v1/responses/" + raw["id"])
        except (KeyError, TypeError, ValueError, AzureError):
            raise AzureError("submit_contract", "El servicio aceptó el envío pero no devolvió un identificador recuperable. No reenvíes.", uncertain=True, request_id=request_id) from None
        self._notify(progress, "submitted", {"operation_id": raw["id"], "operation_location": location}, location)
        if raw.get("status") == "completed":
            result = self._finish(raw, location)
        else:
            result = self.poll(location, progress)
        result[1]["reservation_id"] = metadata["reservation_id"]
        result[1]["client_request_id"] = request_id
        return result

    def poll(self, operation_location: str, progress=None):
        location = self._operation_url(operation_location)
        response_id = urlsplit(location).path.rsplit("/", 1)[-1]
        started = self._monotonic()
        while self._monotonic() - started < self.config.poll_timeout:
            try:
                token = self._token()
                self._check_authorization(location)
                response = self._client.get(location, headers={"Authorization": "Bearer " + token}, follow_redirects=False)
            except (httpx.HTTPError, AzureError) as exc:
                raise AzureError(getattr(exc, "code", "poll_uncertain"), "No se pudo consultar la respuesta conocida. Reanuda por su ID sin volver a enviar.", operation_location=location, uncertain=True) from None
            if response.status_code != 200:
                raise AzureError("poll_http", f"No se pudo consultar Azure Foundry (HTTP {response.status_code}).", operation_location=location, uncertain=True)
            try:
                raw = response.json()
                if not isinstance(raw, dict) or raw.get("id") != response_id:
                    raise ValueError("Wrong response ID")
                status = raw.get("status")
            except (TypeError, ValueError):
                raise AzureError("poll_contract", "La respuesta consultada no corresponde a la operación guardada.", operation_location=location, uncertain=True) from None
            if status == "completed":
                self._notify(progress, "completed", {"operation_id": response_id})
                return self._finish(raw, location)
            if status in {"failed", "cancelled", "incomplete"}:
                raise AzureError("operation_failed", "Azure Foundry terminó sin una respuesta completa. Conserva la evidencia y decide cualquier nuevo envío explícitamente.", operation_location=location, uncertain=False)
            if status not in {"queued", "in_progress"}:
                raise AzureError("poll_contract", "Estado de respuesta no reconocido; no se reenvía.", operation_location=location, uncertain=True)
            self._notify(progress, "processing", {"operation_id": response_id, "message": "Azure Foundry está procesando los fotogramas."}, location)
            self._sleep(self.config.poll_interval)
        raise AzureError("poll_timeout", "La respuesta sigue activa. Reanuda la consulta por su ID sin reenviar los fotogramas.", operation_location=location, uncertain=True)


def normalize_response(raw: dict, steps: list[dict[str, str]], clip: dict, schema_hash: str, sampling: dict, *, model: str = MODEL) -> dict:
    """Validate frame witnesses without inventing events or semantic grounding."""
    texts = []
    for output in raw.get("output", []):
        if isinstance(output, dict) and output.get("type") == "message":
            for part in output.get("content", []):
                if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str):
                    texts.append(part["text"])
    try:
        parsed = json.loads("".join(texts))
        if raw.get("status") != "completed" or not isinstance(parsed, dict):
            raise ValueError("Not completed structured output")
    except (TypeError, ValueError):
        raise AzureError("result_contract", "El modelo no devolvió un informe estructurado completo. Conserva la respuesta privada para revisar.") from None
    timestamps = set(sampling.get("timestamps_ms", []))
    duration = clip.get("duration_ms")
    fields, witnesses, issues = {}, [], []
    for step in steps:
        key = evidence_field_name(step)
        candidate = parsed.get(key)
        if not isinstance(candidate, dict) or candidate.get("status") not in {"observed", "uncertain", "absent"} or not isinstance(candidate.get("occurrences"), list):
            issues.append("missing_or_invalid_candidate_" + key)
            continue
        rows = candidate["occurrences"]
        if candidate["status"] == "absent" and rows:
            issues.append("contradictory_absence_" + key)
        if not rows:
            if candidate["status"] == "absent":
                fields[key] = {"type": "array", "valueArray": []}
            else:
                issues.append("unsubstantiated_or_uncertain_candidate_" + key)
            continue
        encoded_rows = []
        for row in rows:
            if not isinstance(row, dict):
                encoded_rows.append({"type": "integer", "valueInteger": None})
                witnesses.append({})
                continue
            start, end, before, after = (row.get(name) for name in ("start_ms", "end_ms", "before_ms", "after_ms"))
            valid = (all(isinstance(t, int) and not isinstance(t, bool) and t in timestamps for t in (start, end, before, after))
                     and isinstance(duration, int) and 0 <= before <= start < end <= after <= duration
                     and all(isinstance(row.get(name), str) and row[name].strip() for name in ("before_state", "transition", "after_state", "constraints")))
            evidence = row.get("evidence_status")
            if evidence not in {"observed", "uncertain"} or candidate["status"] != "observed" or not valid:
                evidence = "uncertain"
            if not valid:
                start, end = None, None
                issues.append("invalid_frame_witness_" + key)
            values = {
                "SegmentId": {"type": "string", "valueString": ""},
                "Action": {"type": "string", "valueString": row.get("action")},
                "Objects": {"type": "array", "valueArray": [{"type": "string", "valueString": value} for value in row.get("objects", [])]} if isinstance(row.get("objects"), list) else {"type": "array", "valueArray": []},
                "EvidenceStatus": {"type": "string", "valueString": evidence},
                "EstimatedStartMs": {"type": "integer", "valueInteger": start},
                "EstimatedEndMs": {"type": "integer", "valueInteger": end},
            }
            encoded_rows.append({"type": "object", "valueObject": values})
            witnesses.append({name: row.get(name) for name in ("before_ms", "after_ms", "before_state", "transition", "after_state", "constraints")})
        fields[key] = {"type": "array", "valueArray": encoded_rows}
    converted = {"status": "Succeeded", "result": {"apiVersion": API_VERSION, "contents": [{"kind": "audioVisual", "fields": fields}]}}
    result = normalize_cu(converted, steps, clip, schema_hash, API_VERSION, model, "azure")
    # The common normalizer may omit malformed occurrences. Bind evidence by
    # original returned-row position, never by expected sequence or gold times.
    by_position = {index + 1: witness for index, witness in enumerate(witnesses)}
    for event in result["events"]:
        event["visual_evidence"] = by_position.get(int(event["id"].rsplit("-", 1)[-1]), {})
    result["issues"].extend(issues)
    result["analysis_provider"] = PROVIDER
    result["frame_sampling"] = {key: sampling.get(key) for key in (*SAMPLING, "actual_sampling_fps", "actual_frame_count", "timestamps_ms", "manifest_sha256")}
    result["limitations"] = [
        "Se analizan como máximo 49 fotogramas privados distribuidos por todo el vídeo, a una frecuencia adaptada de hasta 2 FPS; movimientos entre muestras pueden perderse.",
        "Los tiempos model_estimate y las descripciones son inferencias del modelo. Comprobar timestamps sólo valida referencias temporales, no la interpretación visual.",
        "No observado significa que esta grabación no aporta evidencia; no prueba que la acción nunca se realizara.",
        "Tres variantes de una sola fuente son una demostración controlada y no certifican seguridad ni precisión general.",
    ]
    return result


def main():
    parser = argparse.ArgumentParser(description="Preparación local de definición Azure Responses; no autentica ni llama al servicio.")
    parser.add_argument("command", choices=["prepare-schema"])
    parser.add_argument("--steps", type=Path, required=True)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    prepared = build_responses_definition(json.loads(args.steps.read_text()), args.model)
    body = json.dumps(prepared, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        with args.output.open("x") as stream:
            stream.write(body)
        args.output.chmod(0o600)
        print(json.dumps({key: prepared[key] for key in ("analyzer_id", "schema_hash")}))
    else:
        print(body)


if __name__ == "__main__":
    main()
