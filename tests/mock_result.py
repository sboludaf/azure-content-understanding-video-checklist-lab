"""Offline HTTP test double. Never installed in the application image."""


def mock_result(steps, clip, progress):
    progress("processing", {"message": "Preparando datos ilustrativos locales; no se analiza el contenido del vídeo."})
    events = []
    checklist = []
    duration = clip["duration_ms"]
    for i, step in enumerate(steps):
        status = "observed" if i < len(steps) - 2 else "uncertain" if i == len(steps) - 2 else "not_observed"
        own = []
        if status == "observed":
            start = round(duration * (i + 0.5) / (len(steps) + 1))
            end = min(duration, start + max(100, round(duration / (len(steps) + 2))))
            event = {"id": f"mock-{i}", "step_id": step["id"], "action": f"Ejemplo ilustrativo: {step['label']}", "objects": ["objeto de ejemplo"], "start_ms": start, "end_ms": end, "time_source": "mock", "evidence_status": "observed"}
            events.append(event)
            own = [event]
        checklist.append({**step, "status": status, "events": own})
    return {"clip": clip, "schema_version": "mock-v1", "api_version": "none-local-mock", "model": "none", "source": "mock", "events": events, "checklist": checklist, "order_changed": False, "issues": ["MOCK: tiempos, acciones y estados sintéticos. No describen el contenido del vídeo ni acreditan A1–A8."], "review": {"notes": "", "decisions": []}}
