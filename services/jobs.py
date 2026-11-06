"""Bounded persistent read-only jobs; SQLite is the queue, async tasks are workers."""
import asyncio
from protocol.models import Incident, Telemetry


class DiagnosticJobs:
    def __init__(self, store, runtime, workers=2):
        self.store, self.runtime = store, runtime
        self.workers = workers
        self.tasks = []
        self.closing = False

    def start(self):
        self.closing = False
        self.store.recover_jobs()
        self.tasks = [asyncio.create_task(self._worker()) for _ in range(self.workers)]

    async def _worker(self):
        while not self.closing:
            claimed = self.store.claim_job()
            if claimed is None:
                await asyncio.sleep(0.05)
                continue
            job_id, snapshot = claimed
            try:
                result = await self.runtime.run(
                    Incident.model_validate(snapshot["incident"]),
                    [Telemetry.model_validate(m) for m in snapshot["history"]])
                self.store.finish_job(job_id, result=result)
            except asyncio.CancelledError:
                job = self.store.get_job(job_id)
                self.store.finish_job(job_id, error="service_stopped", requeue=job["attempts"] < 3)
                raise
            except Exception:
                self.store.finish_job(job_id, error="diagnosis_failed")

    async def wait(self, job_id, timeout=65):
        async with asyncio.timeout(timeout):
            while True:
                job = self.store.get_job(job_id)
                if job["status"] in {"completed", "failed"}:
                    return job
                await asyncio.sleep(0.02)

    async def close(self):
        self.closing = True
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
