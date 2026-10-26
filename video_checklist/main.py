from contextlib import asynccontextmanager
import asyncio
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import uuid
import hmac
from urllib.parse import urlparse

from fastapi import BackgroundTasks, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .authorization import AuthorizationError, load_authorization
from .config import LAB_ID, PROPOSAL_HASH, MAX_BYTES, ROOT, Settings
from .export import html_report
from .ledger import Ledger
from .media import inspect_video
from .store import Store, now


def validate_steps(raw):
    try:
        steps = json.loads(raw)
    except (ValueError, TypeError):
        raise ValueError("La checklist debe ser una lista JSON de pasos.") from None
    if not isinstance(steps, list) or not 3 <= len(steps) <= 6:
        raise ValueError("Define entre 3 y 6 pasos.")
    clean = []
    for step in steps:
        if not isinstance(step, dict) or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", str(step.get("id", ""))):
            raise ValueError("Cada paso necesita un ID único de letras, números, guion o subrayado.")
        label = step.get("label", "")
        if not isinstance(label, str) or not 3 <= len(label.strip()) <= 240:
            raise ValueError("Describe cada paso con entre 3 y 240 caracteres.")
        clean.append({"id": step["id"], "label": label.strip()})
    if len({s["id"] for s in clean}) != len(clean):
        raise ValueError("Los IDs de pasos deben ser únicos.")
    return clean


def default_steps():
    manifest = ROOT / "data/manifest.json"
    if manifest.exists():
        data = json.loads(manifest.read_text())
        steps = data.get("expected_steps") or data.get("steps")
        if steps:
            return [{"id": s["id"], "label": s.get("label", s.get("description", s["id"]))} for s in steps]
    return [
        {"id": "S1", "label": "Preparar un doble de cuerda"},
        {"id": "S2", "label": "Introducir el doble en la anilla"},
        {"id": "S3", "label": "Manipular el doble junto a la anilla"},
        {"id": "S4", "label": "Tirar del ramal largo y tensar"},
    ]


def public_job(doc):
    return {k: v for k, v in doc.items() if k in {"id", "status", "phase", "message", "source", "video_url", "result", "created_at", "updated_at", "operation_id"}} | {"can_resume": bool(doc.get("operation_location")) and doc["status"] == "uncertain"}


def cloud_authorization(settings, steps=None):
    if settings.profile == "azd":
        from .deployment import deployment_configuration
        return deployment_configuration(settings, steps or default_steps())
    auth = load_authorization(settings.role)
    provider = auth.get("provider", "azure_content_understanding")
    if provider not in {"azure_content_understanding", "azure_foundry_responses"}:
        raise AuthorizationError("El proveedor de análisis no está autorizado.")
    if settings.provider != "auto" and settings.provider != provider:
        raise AuthorizationError("El proveedor configurado no coincide con la autorización vigente.")
    return auth


def prepare_definition(auth, steps):
    if auth.get("provider") == "azure_foundry_responses":
        from .foundry import build_responses_definition
        return build_responses_definition(steps, auth["model"], deployment_bound=auth.get("_runtime_profile") == "azd")
    from .azure import build_analyzer_definition
    return build_analyzer_definition(steps, auth["model"], auth["processing_location"])


def analysis_binding(auth):
    source_path = ROOT / "source-manifest.json"
    source_commit = json.loads(source_path.read_text()).get("commit") if source_path.exists() else None
    expected_commit = os.getenv("MVP_EXPECTED_COMMIT")
    authorized_commit = auth.get("_commit")
    if source_commit and expected_commit and source_commit != expected_commit:
        raise AuthorizationError("El checkout o imagen no coincide con el SHA congelado para esta entrega.")
    if authorized_commit and any(value and value != authorized_commit for value in (source_commit, expected_commit)):
        raise AuthorizationError("El SHA ejecutado no coincide con la revisión autorizada.")
    provider = auth.get("provider", "azure_content_understanding")
    return {"provider": provider, "schema_hash": auth["schema_hash"],
            "commit": authorized_commit or expected_commit or source_commit, "model": auth["model"],
            "model_version": auth["model_version"], "api_version": auth.get("api_version", "2025-11-01"),
            "resource_id": auth.get("resource_id") or auth.get("provider_data", {}).get("resource_id"),
            "endpoint": auth["endpoint"], "deployment_name": auth.get("deployment_name"),
            "processing_location": auth["processing_location"]}


def make_adapter(auth, role):
    from .azure import AzureAdapter, AzureConfig
    def revalidate():
        if auth.get("_runtime_profile") == "azd":
            from .deployment import deployment_configuration
            current = deployment_configuration(Settings("azure", auth["_ledger_path"].parent, None, role,
                                                         "azure_foundry_responses", "azd"), auth["_steps"])
        else:
            current = load_authorization(role)
        if current["_authorization_hash"] != auth["_authorization_hash"]:
            raise AuthorizationError("La autorización cambió durante la operación; reconciliar antes de continuar.")
    if auth.get("provider") == "azure_foundry_responses":
        from .foundry import FoundryAdapter, FoundryConfig
        return FoundryAdapter(FoundryConfig(
            endpoint=auth["endpoint"], tenant_id=auth["tenant_id"], principal_id=auth["principal_id"],
            deployment_name=auth["deployment_name"], model=auth["model"], schema_hash=auth["schema_hash"],
            model_version=auth["model_version"], processing_location=auth["processing_location"],
            deployment_bound=auth.get("_runtime_profile") == "azd",
        ), authorize=revalidate)
    return AzureAdapter(AzureConfig(
        endpoint=auth["endpoint"], tenant_id=auth["tenant_id"], principal_id=auth["principal_id"],
        analyzer_id=auth["analyzer_id"], model=auth["model"], schema_hash=auth["schema_hash"],
        processing_location=auth["processing_location"],
    ), authorize=revalidate)


def run_job(store, settings, id_, resume=False):
    ledger = None
    reservation = None
    doc = store.get(id_)

    def progress(status, detail):
        fields = {"status": "processing", "phase": status, "message": detail.get("message", "El servicio está procesando el vídeo.")}
        if status == "submitted":
            fields["operation_id"] = detail.get("operation_id")
            fields["operation_location"] = detail.get("operation_location") or detail.get("operation_url") or detail.get("url")
        # The accepted operation must survive a relay outage. Persist its
        # private recovery URL before attempting another network operation.
        store.update(id_, **fields)
        if status == "submitted" and ledger:
            ledger.update(reservation, "active", fields["operation_id"])

    def record_failure(fields):
        # Local uncertainty is durable even if reconciling the shared ledger
        # fails too. A secondary exception must never discard recovery metadata.
        if ledger and reservation:
            # Keep new submissions blocked until the canonical ledger confirms
            # even a definitive provider failure, including during relay I/O.
            store.update(id_, **(fields | {"status": "uncertain"}))
            try:
                ledger.update(reservation, fields["status"], fields.get("operation_id"))
            except Exception:
                store.update(id_, status="uncertain", phase="ledger", message="No se pudo reconciliar el ledger conjunto. Conserva la reserva y consulta la operación conocida antes de cualquier nuevo envío.")
            else:
                if fields["status"] != "uncertain":
                    store.update(id_, **fields)
        else:
            store.update(id_, **fields)

    try:
        clip = doc["clip"]
        auth = cloud_authorization(settings, doc["steps"])
        if doc.get("analysis_provider") and doc["analysis_provider"] != auth.get("provider", "azure_content_understanding"):
            raise AuthorizationError("Este job pertenece a otro proveedor; conserva su revisión y reconcilia antes de continuar.")
        prepared = prepare_definition(auth, doc["steps"])
        if prepared["schema_hash"] != auth["schema_hash"]:
            raise AuthorizationError("La checklist no coincide con el esquema autorizado; no se reserva ni se envía el vídeo.")
        binding = analysis_binding(auth)
        if resume:
            if doc.get("analysis_binding") != binding:
                raise AuthorizationError("La operación guardada pertenece a otra revisión, modelo o recurso. Conserva su binding histórico y reconcilia antes de consultar o reinterpretar su respuesta.")
        else:
            # Bind before reserving or submitting. Redeploying a prompt or
            # changing source SHA must never relabel an old cloud response.
            store.update(id_, analysis_binding=binding,
                         analysis_provider=auth.get("provider", "azure_content_understanding"))
        ledger = Ledger(auth)
        if resume:
            reservation = doc.get("reservation_id")
            if not reservation or not doc.get("operation_location"):
                raise AuthorizationError("No hay una operación conocida que consultar; no se reenvía.")
        else:
            reservation = ledger.reserve(settings.role, id_, clip["duration_ms"], clip["hash"])
            store.update(id_, reservation_id=reservation, status="submitting", phase="submitting", message="Reserva conjunta persistida; enviando el vídeo una sola vez.")
        adapter = make_adapter(auth, settings.role)
        try:
            if resume:
                raw, metadata = adapter.poll(doc["operation_location"], progress)
            else:
                raw, metadata = adapter.analyze(Path(doc["video_path"]), doc["steps"], {"authorization_confirmed": True, "reservation_id": reservation, "duration_ms": clip["duration_ms"]}, progress)
        finally:
            adapter.close()
        # Raw cloud evidence stays private; never sent to the tracker or browser.
        raw_path = settings.runtime / id_ / "response.private.json"
        raw_path.write_text(json.dumps(raw, ensure_ascii=False))
        raw_path.chmod(0o600)
        if auth.get("provider") == "azure_foundry_responses":
            from .foundry import normalize_response
            sampling_bytes = (settings.runtime / id_ / "frames.private.json").read_bytes()
            sampling = json.loads(sampling_bytes)
            sampling["manifest_sha256"] = hashlib.sha256(sampling_bytes).hexdigest()
            result = normalize_response(raw, doc["steps"], clip, auth["schema_hash"], sampling, model=auth["model"])
        else:
            from .normalizer import normalize
            result = normalize(raw, doc["steps"], clip, schema_version=auth["schema_hash"], api_version="2025-11-01", model=auth["model"], source="azure")
            result["analysis_provider"] = "azure_content_understanding"
        result["service_metadata"] = {k: v for k, v in metadata.items() if k in {"api_version", "model", "model_provenance", "operation_id", "processing_location", "usage", "analysis_provider"}}
        result["model_version"] = auth["model_version"]
        result["region"] = auth["region"]
        result["source_commit"] = auth.get("_commit") or binding["commit"]
        result["analysis_binding"] = {key: binding[key] for key in
            ("provider", "schema_hash", "commit", "model", "model_version", "api_version", "endpoint", "deployment_name")}
        ledger.usage(reservation, metadata.get("usage", {}))
        ledger.update(reservation, "succeeded")
        store.update(id_, result=result, status="succeeded", phase="completed", message="Informe disponible.")
    except AuthorizationError as e:
        record_failure({"status": "uncertain" if reservation or doc.get("operation_location") else "failed", "phase": "authorization", "message": str(e)})
    except Exception as e:
        location = getattr(e, "operation_location", None)
        uncertain = bool(getattr(e, "uncertain", settings.mode == "azure"))
        fields = {"status": "uncertain" if uncertain else "failed", "phase": "error", "message": "El análisis no se completó. Consulta la operación conocida antes de considerar un nuevo envío; la reserva se conserva." if uncertain else "El análisis falló. Corrige la configuración y realiza un nuevo envío explícito."}
        # The adapter's exception text is deliberately sanitized; raw SDK or
        # provider bodies remain private and never enter the user interface.
        from .azure import AzureError
        if isinstance(e, AzureError):
            fields["message"] = str(e)
            fields["error_code"] = e.code
        if location:
            fields["operation_location"] = location
            fields["operation_id"] = urlparse(location).path.rsplit("/", 1)[-1]
        record_failure(fields)


def create_app(settings=None):
    settings = settings or Settings.from_env()
    store = Store(settings.runtime)

    @asynccontextmanager
    async def lifespan(app):
        # Separate app instances cannot process concurrently against the same state.
        with (settings.runtime / "app.lock").open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            store.recover()
            yield
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    app = FastAPI(title="Vídeo → checklist", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.store = store

    @app.middleware("http")
    async def protect_local(request: Request, call_next):
        hostname = request.url.hostname
        allowed = {"127.0.0.1", "localhost", "::1", "testserver"}
        allowed.update(h.strip() for h in os.getenv("MVP_ALLOWED_HOSTS", "").split(",") if h.strip())
        origin = request.headers.get("origin")
        # App Service probes may use an internal Host. Only the read-only
        # hosted health GET without any Origin can bypass the exact host gate.
        hosted_probe = (os.getenv("MVP_HOSTED") == "true" and request.method == "GET"
                        and request.url.path == "/health" and origin is None)
        if hostname not in allowed and not hosted_probe:
            return JSONResponse({"detail": "Host no autorizado."}, status_code=403)
        expected_origin = os.getenv("MVP_BASE_URL", str(request.base_url)).rstrip("/")
        if origin and origin.rstrip("/") != expected_origin:
            return JSONResponse({"detail": "Origen de petición no autorizado."}, status_code=403)
        try:
            size = int(request.headers.get("content-length", "0"))
        except ValueError:
            return JSONResponse({"detail": "Tamaño inválido."}, status_code=400)
        if size > MAX_BYTES + 1024 * 1024:
            return JSONResponse({"detail": "El archivo supera 25 MiB. Recorta o comprime el vídeo."}, status_code=413)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; media-src 'self' blob:; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'"
        return response

    def get_doc(id_):
        doc = store.get(id_)
        if not doc:
            raise HTTPException(404, "No se encuentra la operación. El estado local puede haberse eliminado.")
        return doc

    @app.get("/health")
    def health():
        source = ROOT / "source-manifest.json"
        actual_commit = json.loads(source.read_text()).get("commit") if source.exists() else None
        return {"status": "ok", "lab_id": LAB_ID, "mode": settings.mode, "commit": actual_commit, "expected_commit": os.getenv("MVP_EXPECTED_COMMIT")}

    @app.post("/api/ledger/{operation}")
    async def ledger_relay(operation: str, request: Request):
        token = os.getenv("MVP_LEDGER_RELAY_TOKEN", "")
        supplied = request.headers.get("x-mvp-ledger-token", "")
        if len(token) < 32 or not hmac.compare_digest(token, supplied):
            raise HTTPException(403, "Acceso al ledger no autorizado.")
        try:
            auth = cloud_authorization(settings)
            ledger = Ledger(auth)
            if auth.get("_ledger_url"):
                raise AuthorizationError("El servidor debe alojar el ledger canónico, no otro relay.")
            body = await request.json()
            if operation == "summary":
                return ledger.summary()
            if operation == "reserve":
                role = body.get("role")
                duration = body.get("duration_ms")
                if role not in {"implementer", "tester"} or not isinstance(duration, int) or not 0 < duration <= 90000 or not re.fullmatch(r"[a-f0-9]{64}", str(body.get("clip_hash", ""))) or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", str(body.get("job_id", ""))):
                    raise AuthorizationError("Reserva inválida.")
                return {"reservation_id": ledger.reserve(role, body["job_id"], duration, body["clip_hash"])}
            id_ = body.get("reservation_id", "")
            if not re.fullmatch(r"[a-f0-9-]{36}", str(id_)):
                raise AuthorizationError("Identificador de reserva inválido.")
            if operation == "update":
                ledger.update(id_, body["status"], body.get("operation_id"))
            elif operation == "usage":
                ledger.usage(id_, body.get("observed", {}))
            else:
                raise AuthorizationError("Operación ledger no disponible.")
            return {"ok": True}
        except (ValueError, KeyError, TypeError) as e:
            raise HTTPException(409, str(e) if isinstance(e, AuthorizationError) else "Petición ledger inválida.") from None

    @app.get("/api/config")
    def config():
        warnings = []
        cloud_enabled = False
        if settings.mode == "azure":
            try:
                cloud_authorization(settings)
                cloud_enabled = True
            except AuthorizationError as e:
                warnings.append(str(e))
        example_license = ""
        manifest = ROOT / "data/manifest.json"
        if manifest.exists():
            source = json.loads(manifest.read_text()).get("source", {})
            example_license = source.get("attribution", "") + " " + source.get("license_url", "") + " " + source.get("source_revision_url", "")
        return {"mode": settings.mode, "cloud_enabled": cloud_enabled, "max_size_mib": 25, "max_duration_s": 90, "example_steps": default_steps(), "example_license": example_license.strip(), "warnings": warnings, "sample_available": bool(settings.sample and settings.sample.is_file()) and settings.profile != "azd", "editable_steps": settings.profile == "azd"}

    @app.get("/api/sample")
    def sample():
        if settings.profile == "azd" or not settings.sample or not settings.sample.is_file():
            raise HTTPException(404, "Ejemplo no preparado. Sigue scripts/prepare_media.py o selecciona tu MP4.")
        return FileResponse(settings.sample, media_type="video/mp4", filename="base.mp4")

    @app.post("/api/jobs", status_code=202)
    async def create_job(background: BackgroundTasks, video: UploadFile = File(), steps: str = Form(), license: str = Form("Sin licencia declarada; uso local privado"), idempotency_key: str | None = Header(None)):
        if not idempotency_key or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", idempotency_key):
            raise HTTPException(400, "Falta una clave única de envío válida.")
        try:
            selected = validate_steps(steps)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        if len(license) > 2000:
            raise HTTPException(422, "La atribución/licencia supera 2000 caracteres.")
        filename = re.sub(r"[\x00-\x1f]", "", Path(video.filename or "video.mp4").name)[:160]
        id_ = str(uuid.uuid4())
        folder = settings.runtime / id_
        folder.mkdir(mode=0o700)
        path = folder / "video.mp4"
        keep = False
        try:
            digest = hashlib.sha256()
            size = 0
            with path.open("wb") as out:
                path.chmod(0o600)
                while chunk := await video.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise HTTPException(413, "El vídeo supera 25 MiB. Recorta o comprime el archivo.")
                    out.write(chunk)
                    digest.update(chunk)
            try:
                media = await asyncio.to_thread(inspect_video, path, filename)
                if settings.mode == "azure":
                    auth = cloud_authorization(settings, selected)
            except ValueError as e:
                raise HTTPException(422 if not isinstance(e, AuthorizationError) else 403, str(e)) from None
            clip = {**media, "hash": digest.hexdigest(), "name": filename, "license": license.strip()}
            input_hash = hashlib.sha256(json.dumps([clip["hash"], selected, license, settings.mode], sort_keys=True).encode()).hexdigest()
            doc = {"id": id_, "status": "queued", "phase": "validated", "message": "Vídeo validado localmente.", "source": settings.mode, "video_url": f"/api/jobs/{id_}/video", "video_path": str(path), "clip": clip, "steps": selected, "input_hash": input_hash, "result": None, "created_at": now(), "updated_at": now()}
            if settings.mode == "azure":
                doc["analysis_provider"] = auth.get("provider", "azure_content_understanding")
            try:
                doc, created = store.create(doc, idempotency_key)
            except ValueError as e:
                raise HTTPException(409, str(e)) from None
            if created:
                keep = True
                background.add_task(run_job, store, settings, id_)
            return public_job(doc)
        finally:
            await video.close()
            if not keep:
                path.unlink(missing_ok=True)
                folder.rmdir()

    @app.get("/api/jobs/by-key/{request_key}")
    def job_by_key(request_key: str):
        doc = store.by_key(request_key)
        if not doc:
            raise HTTPException(404, "Todavía no se encuentra una operación registrada con esa clave.")
        return public_job(doc)

    @app.get("/api/jobs/{id_}")
    def job(id_: str):
        return public_job(get_doc(id_))

    @app.get("/api/jobs/{id_}/video")
    def job_video(id_: str):
        path = Path(get_doc(id_)["video_path"])
        if not path.is_file():
            raise HTTPException(410, "Vídeo local ya no disponible; conserva el informe exportado.")
        return FileResponse(path, media_type="video/mp4")

    @app.get("/api/jobs/{id_}/export.json")
    def export_json(id_: str):
        result = get_doc(id_).get("result")
        if not result:
            raise HTTPException(409, "El informe aún no está disponible.")
        return JSONResponse(result, headers={"Content-Disposition": 'attachment; filename="checklist.json"'})

    @app.get("/api/jobs/{id_}/export.html")
    def export_html(id_: str):
        result = get_doc(id_).get("result")
        if not result:
            raise HTTPException(409, "El informe aún no está disponible.")
        return HTMLResponse(html_report(result), headers={"Content-Disposition": 'attachment; filename="checklist.html"'})

    @app.post("/api/jobs/{id_}/review")
    async def review(id_: str, request: Request):
        doc = get_doc(id_)
        if not doc.get("result"):
            raise HTTPException(409, "Espera el informe antes de revisar.")
        try:
            payload = await request.json()
            notes = payload.get("notes", "")
            decisions = payload.get("decisions", [])
            ids = {s["id"] for s in doc["steps"]}
            if not isinstance(notes, str) or len(notes) > 4000 or not isinstance(decisions, list) or len(decisions) > 6:
                raise ValueError
            if any(not isinstance(d, dict) or d.get("step_id") not in ids or d.get("status") not in {"observed", "not_observed", "uncertain"} for d in decisions):
                raise ValueError
        except (ValueError, AttributeError, TypeError):
            raise HTTPException(422, "Revisión inválida: conserva IDs y estados de la checklist.") from None
        result = doc["result"]
        result["review"] = {"notes": notes, "decisions": decisions, "updated_at": now()}
        return public_job(store.update(id_, result=result))

    @app.post("/api/jobs/{id_}/resume", status_code=202)
    def resume(id_: str, background: BackgroundTasks):
        doc = get_doc(id_)
        if doc["status"] != "uncertain" or not doc.get("operation_location") or settings.mode != "azure":
            raise HTTPException(409, "No hay operación Azure conocida para consultar.")
        try:
            cloud_authorization(settings, doc["steps"])
        except AuthorizationError as e:
            raise HTTPException(403, str(e)) from None
        if not store.claim_resume(id_):
            raise HTTPException(409, "La operación ya está siendo consultada por otra ejecución.")
        background.add_task(run_job, store, settings, id_, True)
        return public_job(store.get(id_))

    app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
    return app


app = create_app()
