"""Bounded persistent jobs with finite retries for transient SQLite contention."""
import asyncio
import logging
import sqlite3
from protocol.models import Incident, Telemetry
from agent.contracts import WORKFLOW_VERSION

logger = logging.getLogger(__name__)


def transient_database_error(error):
    code = getattr(error, "sqlite_errorcode", None)
    if code is not None:
        return code & 0xff in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
    return "database is locked" in str(error).lower() or "database table is locked" in str(error).lower()


class DiagnosticJobs:
    def __init__(self, store, runtime, workers=2, retry_delay=.05):
        if workers < 1 or retry_delay <= 0:
            raise ValueError("workers and retry delay must be positive")
        self.store, self.runtime = store, runtime
        self.workers, self.retry_delay = workers, retry_delay
        self.tasks = []
        self.closing = False
        self.errors = {}

    def start(self):
        self.closing = False
        self.errors.clear()
        self.store.recover_jobs()
        self.tasks = [asyncio.create_task(self._worker(i)) for i in range(self.workers)]

    def health(self):
        alive = sum(not task.done() for task in self.tasks)
        ready = not self.closing and alive == self.workers and not self.errors
        return {"status": "ok" if ready else "degraded", "alive_workers": alive,
                "expected_workers": self.workers, "worker_errors": dict(self.errors)}

    async def _database(self, worker_id, operation, *args, **kwargs):
        for attempt in range(5):
            try:
                result = operation(*args, **kwargs)
                self.errors.pop(worker_id, None)
                return result
            except sqlite3.OperationalError as error:
                self.errors[worker_id] = "database_unavailable"
                if not transient_database_error(error) or attempt == 4:
                    raise
                await asyncio.sleep(min(self.retry_delay * 2**attempt, 1))

    async def _worker(self, worker_id):
        try:
            while not self.closing:
                claimed = await self._database(worker_id, self.store.claim_job)
                if claimed is None:
                    await asyncio.sleep(.05)
                    continue
                job_id, snapshot = claimed
                try:
                    if snapshot.get("workflow_version") != WORKFLOW_VERSION:
                        result, error = None, "unsupported_workflow_version"
                    else:
                        try:
                            result = await self.runtime.run(
                                Incident.model_validate(snapshot["incident"]),
                                [Telemetry.model_validate(m) for m in snapshot["history"]])
                            error = None
                        except Exception:
                            result, error = None, "diagnosis_failed"
                    # Retry only persistence here, never repeat the completed model execution.
                    await self._database(worker_id, self.store.finish_job,
                                         job_id, result=result, error=error)
                except asyncio.CancelledError:
                    try:
                        job = self.store.get_job(job_id)
                        await self._database(worker_id, self.store.finish_job, job_id,
                                             error="service_stopped", requeue=job["attempts"] < 3)
                    except Exception:
                        logger.error("job cancellation could not be persisted; restart recovery required")
                    raise
        except asyncio.CancelledError:
            raise
        except Exception:
            self.errors.setdefault(worker_id, "worker_failed")
            logger.error("diagnostic worker stopped worker_id=%s error=%s", worker_id, self.errors[worker_id])

    async def wait(self, job_id, timeout=65):
        async with asyncio.timeout(timeout):
            while True:
                job = self.store.get_job(job_id)
                if job["status"] in {"completed", "failed"}:
                    return job
                await asyncio.sleep(.02)

    async def close(self):
        self.closing = True
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
