from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from datetime import datetime, timezone

ACTIVE = {"queued", "validating", "submitting", "processing"}


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, runtime: Path):
        self.runtime = runtime
        self.path = runtime / "jobs.sqlite3"
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, request_key TEXT UNIQUE, status TEXT, document TEXT)")
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

    def recover(self):
        with self.connect() as db:
            rows = db.execute("SELECT id,document FROM jobs WHERE status IN ('queued','validating','submitting','processing')").fetchall()
            for id_, raw in rows:
                doc = json.loads(raw)
                doc.update(status="uncertain", phase="interrupted", message="El backend se interrumpió. Consulta la operación conocida; no se ha reenviado el vídeo.", updated_at=now())
                db.execute("UPDATE jobs SET status=?,document=? WHERE id=?", (doc["status"], json.dumps(doc), id_))

    def create(self, doc, request_key):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT document FROM jobs WHERE request_key=?", (request_key,)).fetchone()
            if old:
                existing = json.loads(old[0])
                if existing["input_hash"] != doc["input_hash"]:
                    raise ValueError("La clave de envío ya corresponde a otro vídeo o checklist.")
                return existing, False
            active = db.execute("SELECT id FROM jobs WHERE status IN ('queued','validating','submitting','processing','uncertain') LIMIT 1").fetchone()
            if active:
                raise ValueError("Hay una operación activa o incierta. Consulta su estado antes de enviar otra.")
            db.execute("INSERT INTO jobs VALUES (?,?,?,?)", (doc["id"], request_key, doc["status"], json.dumps(doc)))
            return doc, True

    def get(self, id_):
        with self.connect() as db:
            row = db.execute("SELECT document FROM jobs WHERE id=?", (id_,)).fetchone()
        return json.loads(row[0]) if row else None

    def by_key(self, request_key):
        with self.connect() as db:
            row = db.execute("SELECT document FROM jobs WHERE request_key=?", (request_key,)).fetchone()
        return json.loads(row[0]) if row else None

    def claim_resume(self, id_):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT document FROM jobs WHERE id=? AND status='uncertain'", (id_,)).fetchone()
            if not row:
                return False
            doc = json.loads(row[0])
            if not doc.get("operation_location"):
                return False
            doc.update(status="processing", phase="resuming", message="Consultando la misma operación; sin enviar otro análisis.", updated_at=now())
            db.execute("UPDATE jobs SET status='processing',document=? WHERE id=? AND status='uncertain'", (json.dumps(doc), id_))
            return True

    def update(self, id_, **fields):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT document FROM jobs WHERE id=?", (id_,)).fetchone()
            if not row:
                raise KeyError(id_)
            doc = json.loads(row[0])
            doc.update(fields, updated_at=now())
            db.execute("UPDATE jobs SET status=?,document=? WHERE id=?", (doc["status"], json.dumps(doc), id_))
        return doc
