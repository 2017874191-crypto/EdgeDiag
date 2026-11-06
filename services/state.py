"""SQLite domain store. All ingestion decisions and incident events commit atomically."""
import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from protocol.models import Incident, Telemetry
from services.anomaly import detect, stable_type
from agent.contracts import WORKFLOW_VERSION

MAX_FUTURE_SKEW_MS = 60_000


class ConflictError(ValueError):
    pass


class CapacityError(RuntimeError):
    pass


class StateStore:
    def __init__(self, path=":memory:", history_size=300, capacity=256, clock_ms=None):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self.clock_ms = clock_ms or (lambda: int(time.time()*1000))
        self.history_size, self.capacity = history_size, capacity
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS telemetry (
                id INTEGER PRIMARY KEY, device_id TEXT NOT NULL, boot_id TEXT NOT NULL,
                seq INTEGER NOT NULL, timestamp_ms INTEGER NOT NULL, body TEXT NOT NULL,
                UNIQUE(device_id, boot_id, seq));
            CREATE INDEX IF NOT EXISTS telemetry_time ON telemetry(device_id, timestamp_ms);
            CREATE TABLE IF NOT EXISTS devices (
                device_id TEXT PRIMARY KEY, watermark_ms INTEGER NOT NULL, received_ms INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS incidents (
                incident_id TEXT PRIMARY KEY, device_id TEXT NOT NULL, type TEXT NOT NULL,
                active INTEGER NOT NULL, body TEXT NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS active_incident ON incidents(device_id,type) WHERE active=1;
            CREATE TABLE IF NOT EXISTS incident_events (
                event_id INTEGER PRIMARY KEY, incident_id TEXT NOT NULL, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS diagnostic_jobs (
                job_id TEXT PRIMARY KEY, request_key TEXT NOT NULL UNIQUE, fingerprint TEXT NOT NULL,
                status TEXT NOT NULL, snapshot TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                result TEXT, error TEXT, created_ms INTEGER NOT NULL);
        """)
        # Additive migration: preserve raw samples, quarantine legacy future rows.
        columns = {r["name"] for r in self.db.execute("PRAGMA table_info(telemetry)")}
        with self.transaction() as db:
            if "clock_valid" not in columns:
                db.execute("ALTER TABLE telemetry ADD COLUMN clock_valid INTEGER NOT NULL DEFAULT 1")
            if "clock_checked_ms" not in columns:
                db.execute("ALTER TABLE telemetry ADD COLUMN clock_checked_ms INTEGER")
            checked_ms = self.clock_ms()
            db.execute("UPDATE telemetry SET clock_valid=0,clock_checked_ms=? WHERE clock_valid=1 AND timestamp_ms>?",
                       (checked_ms, checked_ms + MAX_FUTURE_SKEW_MS))
            db.execute("""UPDATE devices SET watermark_ms=COALESCE(
                (SELECT MAX(timestamp_ms) FROM telemetry
                 WHERE telemetry.device_id=devices.device_id AND clock_valid=1), -1)""")

    @contextmanager
    def transaction(self):
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield self.db
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def _history(self, db, device_id, limit=300):
        rows = db.execute(
            "SELECT body FROM telemetry WHERE device_id=? AND clock_valid=1 ORDER BY timestamp_ms DESC,id DESC LIMIT ?",
            (device_id, limit)).fetchall()
        return [Telemetry.model_validate_json(r["body"]) for r in reversed(rows)]

    def history(self, device_id, limit=100):
        if not 1 <= limit <= 300:
            raise ValueError("limit must be 1..300")
        with self._lock:
            return self._history(self.db, device_id, limit)

    @property
    def last_seen(self):
        with self._lock:
            return {r["device_id"]: r["watermark_ms"] for r in self.db.execute("SELECT * FROM devices")}

    def ingest(self, message):
        """Late samples persist for audit, but cannot rewrite current incident state."""
        body = message.model_dump_json()
        with self.transaction() as db:
            duplicate = db.execute(
                "SELECT body FROM telemetry WHERE device_id=? AND boot_id=? AND seq=?",
                (message.device_id, message.boot_id, message.seq)).fetchone()
            if duplicate:
                if Telemetry.model_validate_json(duplicate["body"]) != message:
                    raise ConflictError("sequence reused with different payload")
                return {"accepted": False, "reason": "duplicate", "incidents": []}
            now = self.clock_ms()
            clock_valid = message.timestamp_ms <= now + MAX_FUTURE_SKEW_MS
            db.execute("INSERT INTO telemetry(device_id,boot_id,seq,timestamp_ms,body,clock_valid,clock_checked_ms) VALUES(?,?,?,?,?,?,?)",
                       (message.device_id, message.boot_id, message.seq, message.timestamp_ms, body, int(clock_valid), now))
            if not clock_valid:
                return {"accepted": True, "reason": "clock_quarantined", "incidents": []}
            current = db.execute("SELECT watermark_ms FROM devices WHERE device_id=?", (message.device_id,)).fetchone()
            watermark = current["watermark_ms"] if current else -1
            db.execute("""INSERT INTO devices VALUES(?,?,?)
                          ON CONFLICT(device_id) DO UPDATE SET
                          watermark_ms=MAX(watermark_ms,excluded.watermark_ms), received_ms=excluded.received_ms""",
                       (message.device_id, message.timestamp_ms, now))
            if message.timestamp_ms <= watermark:
                return {"accepted": True, "reason": "late", "incidents": []}
            history = self._history(db, message.device_id, self.history_size)
            stable = stable_type(message, history)
            active = [Incident.model_validate_json(r["body"]) for r in db.execute(
                "SELECT body FROM incidents WHERE device_id=? AND active=1", (message.device_id,))]
            candidates = detect(message, history)
            changed = []
            for previous in active:
                # Invalid measurements cannot prove that a physical fault recovered.
                if stable is not None and stable[0] != "sensor_invalid" and stable[0] != previous.type:
                    previous.status = "resolved"
                    previous.revision += 1
                    self._save_incident(db, previous, False)
                    self._event(db, previous, "resolved", message)
                    changed.append(previous)
            if candidates:
                candidate = candidates[0]
                previous = next((i for i in active if i.type == candidate.type), None)
                if previous:
                    candidate = previous
                    candidate.revision += 1
                    candidate.occurrences += 1
                    candidate.evidence = candidates[0].evidence
                    kind = "updated"
                else:
                    kind = "opened"
                self._save_incident(db, candidate, True)
                self._event(db, candidate, kind, message)
                changed.append(candidate)
            return {"accepted": True, "reason": "accepted", "incidents": changed}

    def quarantined(self, device_id, limit=100):
        with self._lock:
            return [{"reason": "future_clock", "checked_ms": r["clock_checked_ms"],
                     "max_future_skew_ms": MAX_FUTURE_SKEW_MS, "telemetry": json.loads(r["body"])}
                    for r in self.db.execute(
                        "SELECT body,clock_checked_ms FROM telemetry WHERE device_id=? AND clock_valid=0 ORDER BY id DESC LIMIT ?",
                        (device_id, limit))]

    def accept(self, message):
        return self.ingest(message)["accepted"]

    def _save_incident(self, db, incident, active):
        db.execute("INSERT OR REPLACE INTO incidents VALUES(?,?,?,?,?)",
                   (incident.incident_id, incident.device_id, incident.type, int(active), incident.model_dump_json()))

    def _event(self, db, incident, kind, message):
        db.execute("INSERT INTO incident_events(incident_id,body) VALUES(?,?)",
                   (incident.incident_id, json.dumps({
                       "kind": kind, "revision": incident.revision, "timestamp_ms": message.timestamp_ms,
                       "boot_id": message.boot_id, "seq": message.seq}, ensure_ascii=False)))

    def get_incident(self, incident_id):
        with self._lock:
            row = self.db.execute("SELECT body FROM incidents WHERE incident_id=?", (incident_id,)).fetchone()
            return Incident.model_validate_json(row["body"]) if row else None

    def list_incidents(self, limit=100):
        with self._lock:
            return [Incident.model_validate_json(r["body"]) for r in self.db.execute(
                "SELECT body FROM incidents ORDER BY rowid DESC LIMIT ?", (limit,))]

    def incident_events(self, incident_id):
        with self._lock:
            return [json.loads(r["body"]) for r in self.db.execute(
                "SELECT body FROM incident_events WHERE incident_id=? ORDER BY event_id", (incident_id,))]

    def submit_job(self, incident_id, request_key=None, expected_revision=None):
        with self.transaction() as db:
            request_key = "client:" + request_key if request_key else None
            # Explicit key binds request semantics, so retrying after evidence changes replays original job.
            fingerprint = hashlib.sha256(json.dumps(
                [incident_id, expected_revision, WORKFLOW_VERSION]).encode()).hexdigest()
            if request_key:
                prior = db.execute("SELECT * FROM diagnostic_jobs WHERE request_key=?", (request_key,)).fetchone()
                if prior:
                    if prior["fingerprint"] != fingerprint:
                        raise ConflictError("request key reused with different parameters")
                    return self._job(prior), False
            incident = self.get_incident(incident_id)
            if incident is None:
                raise KeyError(incident_id)
            if expected_revision is not None and expected_revision != incident.revision:
                raise ConflictError("incident revision changed")
            key = request_key or f"snapshot:{incident_id}:{incident.revision}:{WORKFLOW_VERSION}"
            prior = db.execute("SELECT * FROM diagnostic_jobs WHERE request_key=?", (key,)).fetchone()
            if prior:
                return self._job(prior), False
            if db.execute("SELECT COUNT(*) FROM diagnostic_jobs WHERE status IN ('queued','running')").fetchone()[0] >= self.capacity:
                raise CapacityError("diagnosis queue full")
            snapshot = {"incident": incident.model_dump(mode="json"),
                        "history": [m.model_dump(mode="json") for m in self._history(db, incident.device_id)],
                        "workflow_version": WORKFLOW_VERSION}
            job_id = uuid.uuid4().hex
            db.execute("""INSERT INTO diagnostic_jobs
                          (job_id,request_key,fingerprint,status,snapshot,created_ms) VALUES(?,?,?,'queued',?,?)""",
                       (job_id, key, fingerprint, json.dumps(snapshot, ensure_ascii=False), int(time.time()*1000)))
            return self._job(db.execute("SELECT * FROM diagnostic_jobs WHERE job_id=?", (job_id,)).fetchone()), True

    def _job(self, row):
        return {"job_id": row["job_id"], "request_key": row["request_key"], "status": row["status"],
                "attempts": row["attempts"], "result": json.loads(row["result"]) if row["result"] else None,
                "error": row["error"], "created_ms": row["created_ms"]}

    def get_job(self, job_id):
        with self._lock:
            row = self.db.execute("SELECT * FROM diagnostic_jobs WHERE job_id=?", (job_id,)).fetchone()
            return self._job(row) if row else None

    def recover_jobs(self):
        # Single process ownership: read-only diagnosis may rerun after a crash, at most 3 attempts.
        with self.transaction() as db:
            db.execute("""UPDATE diagnostic_jobs SET status=CASE WHEN attempts<3 THEN 'queued' ELSE 'failed' END,
                          error='process_interrupted' WHERE status='running'""")

    def claim_job(self):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM diagnostic_jobs WHERE status='queued' ORDER BY created_ms,rowid LIMIT 1").fetchone()
            if row is None:
                return None
            db.execute("UPDATE diagnostic_jobs SET status='running',attempts=attempts+1,error=NULL WHERE job_id=?", (row["job_id"],))
            return row["job_id"], json.loads(row["snapshot"])

    def finish_job(self, job_id, result=None, error=None, requeue=False):
        with self.transaction() as db:
            db.execute("UPDATE diagnostic_jobs SET status=?,result=?,error=? WHERE job_id=? AND status='running'",
                       ("queued" if requeue else ("failed" if error else "completed"),
                        json.dumps(result, ensure_ascii=False) if result else None, error, job_id))

    def close(self):
        self.db.close()
