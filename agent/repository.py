"""Durable run records. Single API process for V1; no distributed task scheduler."""
import json
import sqlite3
from pathlib import Path
from threading import RLock


class RunRepository:
    def __init__(self, path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS runs (trace_id TEXT PRIMARY KEY, body TEXT NOT NULL)")
        self.db.commit()
        # An interrupted request is visible and retryable, never reported as completed.
        for row in self.db.execute("SELECT body FROM runs").fetchall():
            run = json.loads(row[0])
            if run["status"] == "running":
                run.update(status="interrupted", error="process_restarted")
                self.save(run)

    def save(self, run):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO runs VALUES (?, ?)",
                            (run["trace_id"], json.dumps(run, ensure_ascii=False)))

    def get(self, trace_id):
        with self.lock:
            row = self.db.execute("SELECT body FROM runs WHERE trace_id=?", (trace_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def close(self):
        self.db.close()
