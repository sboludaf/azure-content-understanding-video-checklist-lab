"""Normalize documented GA fields without inventing events or temporal grounding."""
from __future__ import annotations

from copy import deepcopy
import math
from typing import Any

from .azure import evidence_field_name


def _value(field: Any) -> Any:
    if not isinstance(field, dict):
        return None
    kind = field.get("type")
    if kind == "array":
        values = field.get("valueArray")
        return [_value(item) for item in values] if isinstance(values, list) else None
    if kind == "object":
        values = field.get("valueObject")
        return {key: _value(value) for key, value in values.items()} if isinstance(values, dict) else None
    key = {"string": "valueString", "integer": "valueInteger", "number": "valueNumber",
           "boolean": "valueBoolean"}.get(kind)
    return field.get(key) if key else None


def _integer(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value != int(value):
        return None
    return int(value)


def _interval(start: Any, end: Any, duration: int | None) -> tuple[int | None, int | None]:
    start, end = _integer(start), _integer(end)
    if duration is None or start is None or end is None or not 0 <= start < end <= duration:
        return None, None
    return start, end


def normalize(raw: dict[str, Any], steps: list[dict[str, str]], clip: dict[str, Any],
              schema_version: str, api_version: str, model: str,
              source: str = "azure") -> dict[str, Any]:
    """Supports opaque candidate arrays and historic ``Evidence_<id>/Segments``.

    Opaque fields map through the authorized ID and label. A candidate field
    supplies its ID only for occurrences actually returned;
    an explicit empty array never manufactures an event. Missing candidate
    arrays are uncertainty, rather than evidence that the action is absent.
    Service segment bounds are used only for a matching SegmentId. Generated
    EstimatedStartMs/EstimatedEndMs remain ``model_estimate``. Invalid intervals
    are removed and their event is uncertain, preventing a misleading seek.
    """
    clip_keys = ("clip_id", "sha256", "hash", "duration_ms", "filename", "name", "source_url", "license",
                 "attribution", "transformations", "variant")
    safe_clip = {key: deepcopy(clip[key]) for key in clip_keys if key in clip}
    duration = _integer(clip.get("duration_ms"))
    known = {step["id"] for step in steps}
    issues: list[str] = []
    events: list[dict[str, Any]] = []
    unavailable_steps: set[str] = set()
    contract_valid = True
    if not isinstance(raw, dict) or str(raw.get("status", "")).lower() != "succeeded":
        contents = []
        contract_valid = False
        issues.append("analysis_not_succeeded")
    else:
        result = raw.get("result", {})
        contents = result.get("contents") if isinstance(result, dict) else None
        if not isinstance(contents, list):
            contents = []
            contract_valid = False
            issues.append("missing_contents")
        if isinstance(result, dict) and result.get("apiVersion") != api_version:
            contract_valid = False
            issues.append("api_version_mismatch")
            contents = []
    if not contents:
        contract_valid = False
        issues.append("no_audio_visual_contents")
    if duration is None or duration <= 0:
        issues.append("invalid_clip_duration")
        contract_valid = False
    for ci, content in enumerate(contents):
        if not isinstance(content, dict) or content.get("kind") != "audioVisual":
            contract_valid = False
            issues.append(f"content_{ci}_not_audio_visual")
            continue
        segments = content.get("segments", [])
        segment_by_id = {}
        if isinstance(segments, list):
            for segment in segments:
                if isinstance(segment, dict) and isinstance(segment.get("segmentId"), str):
                    sid = segment["segmentId"]
                    if sid in segment_by_id:
                        issues.append(f"duplicate_segment_{ci}_{sid}")
                        segment_by_id[sid] = None
                    else:
                        segment_by_id[sid] = segment
        fields = content.get("fields", {})
        if not isinstance(fields, dict):
            contract_valid = False
            issues.append(f"content_{ci}_missing_segments_field")
            continue
        entries: list[tuple[str | None, Any]] = []
        if "Segments" in fields:
            # An explicit historical payload takes precedence if a response
            # unexpectedly contains both formats; never count it twice.
            historical = _value(fields["Segments"])
            if not isinstance(historical, list):
                contract_valid = False
                issues.append(f"content_{ci}_missing_segments_field")
                continue
            entries = [(None, entry) for entry in historical]
        elif any(evidence_field_name(step) in fields for step in steps):
            for step in steps:
                sid = step["id"]
                field_name = evidence_field_name(step)
                candidate_entries = _value(fields.get(field_name))
                if not isinstance(candidate_entries, list):
                    unavailable_steps.add(sid)
                    issues.append(f"content_{ci}_missing_or_invalid_{field_name}")
                    continue
                entries.extend((sid, entry) for entry in candidate_entries)
        elif any(f"Evidence_{sid}" in fields for sid in known):
            for step in steps:
                sid = step["id"]
                field_name = f"Evidence_{sid}"
                candidate_entries = _value(fields.get(field_name))
                if not isinstance(candidate_entries, list):
                    unavailable_steps.add(sid)
                    issues.append(f"content_{ci}_missing_or_invalid_{field_name}")
                    continue
                entries.extend((sid, entry) for entry in candidate_entries)
        else:
            contract_valid = False
            issues.append(f"content_{ci}_missing_segments_field")
            continue
        for ei, (candidate_id, entry) in enumerate(entries):
            event_id = f"event-{ci + 1}-{ei + 1}"
            if not isinstance(entry, dict):
                issues.append(f"{event_id}_malformed")
                if candidate_id is None:
                    contract_valid = False
                else:
                    unavailable_steps.add(candidate_id)
                continue
            sid = candidate_id if candidate_id is not None else entry.get("StepId")
            action = entry.get("Action")
            action = action.strip()[:2000] if isinstance(action, str) else ""
            evidence = entry.get("EvidenceStatus")
            if evidence not in ("observed", "uncertain"):
                evidence = "uncertain"
                issues.append(f"{event_id}_missing_evidence_status")
            if sid not in known:
                sid = "unknown"
                evidence = "uncertain"
                issues.append(f"{event_id}_unknown_step")
            if not action:
                evidence = "uncertain"
                issues.append(f"{event_id}_missing_action")
            segment = segment_by_id.get(entry.get("SegmentId"))
            if segment is not None:
                start, end = _interval(segment.get("startTimeMs"), segment.get("endTimeMs"), duration)
                time_source = "service_segment"
            else:
                start, end = _interval(entry.get("EstimatedStartMs"), entry.get("EstimatedEndMs"), duration)
                time_source = "model_estimate"
            if start is None:
                evidence = "uncertain"
                time_source = "unavailable"
                issues.append(f"{event_id}_invalid_or_missing_time")
            objects = entry.get("Objects")
            objects = [obj.strip()[:200] for obj in objects if isinstance(obj, str) and obj.strip()] if isinstance(objects, list) else []
            events.append({"id": event_id, "step_id": sid, "action": action,
                           "objects": objects, "start_ms": start, "end_ms": end,
                           "time_source": time_source, "evidence_status": evidence})
    checklist = []
    for step in steps:
        attached = [event for event in events if event["step_id"] == step["id"]]
        if any(event["evidence_status"] == "observed" for event in attached):
            status = "observed"
        elif attached or not contract_valid or step["id"] in unavailable_steps:
            status = "uncertain"
        else:
            status = "not_observed"
        checklist.append({"id": step["id"], "label": step["label"], "status": status,
                          "events": [event["id"] for event in attached]})
    order = {step["id"]: i for i, step in enumerate(steps)}
    chronological = sorted((event for event in events if event["evidence_status"] == "observed"),
                           key=lambda event: (event["start_ms"], event["end_ms"], event["id"]))
    first = []
    seen = set()
    for event in chronological:
        if event["step_id"] not in seen:
            first.append(order[event["step_id"]])
            seen.add(event["step_id"])
    order_changed = any(a > b for a, b in zip(first, first[1:]))
    return {"clip": safe_clip, "schema_version": schema_version, "api_version": api_version,
            "model": model, "source": source, "events": events, "checklist": checklist,
            "order_changed": order_changed, "issues": issues,
            "limitations": [
                "No observado significa que esta grabación no aporta evidencia; no prueba que la acción nunca se realizara.",
                "Los tiempos model_estimate son estimaciones del modelo, no grounding temporal del servicio.",
                "Tres variantes de una sola fuente son una demostración controlada y no miden precisión general ni certifican seguridad.",
            ],
            "review": {"notes": "", "decisions": []}}
