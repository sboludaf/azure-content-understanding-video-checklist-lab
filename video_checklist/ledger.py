"""One canonical SQLite ledger shared by Implementer and Tester, never auto-reset."""
from contextlib import contextmanager
from decimal import Decimal
import json
import sqlite3
import uuid
import os
from pathlib import Path
import httpx

from .authorization import AuthorizationError
from .config import LAB_ID, PROPOSAL_HASH
from .store import now


class Ledger:
    def __new__(cls, auth):
        if auth.get("_ledger_url"):
            return RemoteLedger(auth)
        return super().__new__(cls)

    def __init__(self, auth):
        self.auth = auth
        self.path = auth["_ledger_path"]
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS identity (lab_id TEXT PRIMARY KEY, proposal_hash TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS reservations (id TEXT PRIMARY KEY, role TEXT, job_id TEXT UNIQUE, duration_ms INTEGER, cost TEXT, status TEXT, created_at TEXT, operation_id TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS evidence (reservation_id TEXT PRIMARY KEY, clip_hash TEXT, schema_hash TEXT, commit_sha TEXT, observed_usage TEXT)")
            db.execute("INSERT OR IGNORE INTO identity VALUES (?,?)", (LAB_ID, PROPOSAL_HASH))
            identities = db.execute("SELECT lab_id,proposal_hash FROM identity").fetchall()
            if identities != [(LAB_ID, PROPOSAL_HASH)]:
                raise AuthorizationError("Ledger pertenece a otra identidad; no se reutiliza ni resetea.")
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def reserve(self, role, job_id, duration_ms, clip_hash=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT id,role,duration_ms FROM reservations WHERE job_id=?", (job_id,)).fetchone()
            if existing:
                evidence = db.execute("SELECT clip_hash FROM evidence WHERE reservation_id=?", (existing[0],)).fetchone()
                if existing[1] != role or existing[2] != duration_ms or (evidence and evidence[0] != clip_hash):
                    raise AuthorizationError("La reserva previa corresponde a otra entrada.")
                return existing[0]
            rows = db.execute("SELECT role,duration_ms,cost,status FROM reservations").fetchall()
            if any(r[3] in {"reserved", "active", "uncertain"} for r in rows):
                raise AuthorizationError("El ledger tiene una operación activa o incierta; no se envía otra.")
            if len(rows) >= self.auth["max_attempts"] or sum(r[0] == role for r in rows) >= self.auth["max_attempts_per_role"][role]:
                raise AuthorizationError("Límite conjunto o de rol de análisis agotado.")
            if Decimal(sum(r[1] for r in rows) + duration_ms) / Decimal(60000) > self.auth["_minutes"]:
                raise AuthorizationError("Minutos conjuntos agotados.")
            role_minutes = self.auth.get("_minutes_per_role")
            if role_minutes is not None and Decimal(sum(r[1] for r in rows if r[0] == role) + duration_ms) / Decimal(60000) > role_minutes[role]:
                raise AuthorizationError("Minutos de este actor agotados; se conserva la reserva del otro actor.")
            if self.auth["_budget"] is not None and sum((Decimal(r[2]) for r in rows), Decimal(0)) + self.auth["_bound"] > self.auth["_budget"]:
                raise AuthorizationError("Techo económico conjunto agotado.")
            id_ = str(uuid.uuid4())
            db.execute("INSERT INTO reservations VALUES (?,?,?,?,?,?,?,?)", (id_, role, job_id, duration_ms, str(self.auth["_bound"]), "reserved", now(), None))
            db.execute("INSERT INTO evidence VALUES (?,?,?,?,?)", (id_, clip_hash, self.auth.get("schema_hash"), self.auth.get("_commit"), None))
            return id_

    def update(self, id_, status, operation_id=None):
        if status not in {"active", "succeeded", "failed", "uncertain"}:
            raise ValueError("Estado ledger inválido")
        with self.connect() as db:
            db.execute("UPDATE reservations SET status=?,operation_id=COALESCE(?,operation_id) WHERE id=?", (status, operation_id, id_))

    def summary(self):
        with self.connect() as db:
            rows = db.execute("SELECT role,duration_ms,cost,status FROM reservations").fetchall()
        return {"lab_id": LAB_ID, "attempts_reserved": len(rows), "attempts_per_role": {actor: sum(r[0] == actor for r in rows) for actor in ("implementer", "tester")}, "minutes_reserved": sum(r[1] for r in rows) / 60000, "minutes_reserved_per_role": {actor: sum(r[1] for r in rows if r[0] == actor) / 60000 for actor in ("implementer", "tester")}, "cost_reserved_eur": str(sum((Decimal(r[2]) for r in rows), Decimal(0))), "cost_observed_eur": None, "cost_mode": self.auth.get("_cost_mode", "monetary_cap"), "monetary_cap_eur": str(self.auth["_budget"]) if self.auth["_budget"] is not None else None, "states": {s: sum(r[3] == s for r in rows) for s in ["reserved", "active", "uncertain", "succeeded", "failed"]}}

    def usage(self, id_, observed):
        with self.connect() as db:
            db.execute("UPDATE evidence SET observed_usage=? WHERE reservation_id=?", (json.dumps(observed), id_))


class RemoteLedger:
    """The hosted ledger is authoritative for both actors and every clean clone."""
    def __init__(self, auth):
        self.url = auth["_ledger_url"]
        path = Path(os.environ["MVP_LEDGER_RELAY_TOKEN_PATH"])
        self.token = path.read_text().strip()
        if len(self.token) < 32:
            raise AuthorizationError("Falta acceso privado al ledger conjunto.")

    def call(self, operation, payload=None):
        try:
            response = httpx.post(self.url + "/" + operation, json=payload or {}, headers={"X-MVP-Ledger-Token": self.token}, timeout=30, follow_redirects=False, trust_env=False)
            if response.status_code != 200:
                raise AuthorizationError("El ledger remoto rechazó la operación; no se analiza el vídeo.")
            return response.json()
        except httpx.HTTPError:
            raise AuthorizationError("Ledger remoto incierto. Consulta el saldo antes de cualquier nuevo análisis.") from None

    def reserve(self, role, job_id, duration_ms, clip_hash=None):
        return self.call("reserve", {"role": role, "job_id": job_id, "duration_ms": duration_ms, "clip_hash": clip_hash})["reservation_id"]

    def update(self, id_, status, operation_id=None):
        self.call("update", {"reservation_id": id_, "status": status, "operation_id": operation_id})

    def usage(self, id_, observed):
        self.call("usage", {"reservation_id": id_, "observed": observed})

    def summary(self):
        return self.call("summary")
