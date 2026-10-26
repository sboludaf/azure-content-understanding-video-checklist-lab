"""Synthetic UI demonstration. Does not inspect the user's video's contents."""
import time


def fixture_result(steps, clip, progress):
    progress("processing", {"message": "Preparando datos ilustrativos locales; no se analiza el contenido del vídeo."})
    time.sleep(0.15)
    events = []
    checklist = []
    duration = clip["duration_ms"]
    for i, step in enumerate(steps):
        status = "observed" if i < len(steps) - 2 else "uncertain" if i == len(steps) - 2 else "not_observed"
        own = []
        if status == "observed":
            start = round(duration * (i + 0.5) / (len(steps) + 1))
            end = min(duration, start + max(100, round(duration / (len(steps) + 2))))
            event = {"id": f"fixture-{i}", "step_id": step["id"], "action": f"Ejemplo ilustrativo: {step['label']}", "objects": ["objeto de ejemplo"], "start_ms": start, "end_ms": end, "time_source": "fixture", "evidence_status": "observed"}
            events.append(event)
            own = [event]
        checklist.append({**step, "status": status, "events": own})
    return {"clip": clip, "schema_version": "fixture-v1", "api_version": "none-local-fixture", "model": "none", "source": "fixture", "events": events, "checklist": checklist, "order_changed": False, "issues": ["FIXTURE: tiempos, acciones y estados sintéticos. No describen el contenido del vídeo ni acreditan A1–A8."], "review": {"notes": "", "decisions": []}}
