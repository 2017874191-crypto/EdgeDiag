"""AUD-001..003: former counterexamples, now ordinary regression tests."""
import asyncio
import json
import sqlite3
from services.jobs import DiagnosticJobs
from services.state import StateStore
from agent.knowledge import KnowledgeBase
from agent.repository import RunRepository
from agent.runtime import DiagnosticRuntime
from protocol.models import Telemetry
from tests.test_service import message


def seed(store):
    for n in range(1, 4):
        result = store.ingest(Telemetry.model_validate(message(n, n*1000)))
    return result["incidents"][0].incident_id


def test_worker_survives_transient_claim_failure(monkeypatch):
    async def scenario():
        store = StateStore()
        iid = seed(store)
        job, _ = store.submit_job(iid)
        repo = RunRepository(":memory:")
        workers = DiagnosticJobs(store, DiagnosticRuntime(repo, KnowledgeBase()), workers=1)
        claim = store.claim_job
        calls = 0
        def flaky():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise sqlite3.OperationalError("injected database is locked")
            return claim()
        monkeypatch.setattr(store, "claim_job", flaky)
        workers.start()
        try:
            for _ in range(30):
                if store.get_job(job["job_id"])["status"] == "completed":
                    break
                await asyncio.sleep(0.01)
            assert store.get_job(job["job_id"])["status"] == "completed", (
                "transient failure left queued work without a live worker")
        finally:
            await workers.close()
            repo.close()
            store.close()
    asyncio.run(scenario())


def test_bad_clock_does_not_suppress_new_boot_anomalies():
    store = StateStore()
    try:
        store.ingest(Telemetry.model_validate(message(1, 10**15, boot="bad-clock", temperature=50, vibration=.4)))
        for n in range(1, 4):
            store.ingest(Telemetry.model_validate(message(n, n*1000, boot="correct-clock")))
        assert len(store.list_incidents()) == 1, "all corrected-clock samples were treated as late"
    finally:
        store.close()


def test_worker_rejects_unsupported_workflow_version():
    async def scenario():
        store = StateStore()
        iid = seed(store)
        job, _ = store.submit_job(iid)
        row = store.db.execute("SELECT snapshot FROM diagnostic_jobs").fetchone()
        snapshot = json.loads(row["snapshot"])
        snapshot["workflow_version"] = "unknown-future-workflow"
        store.db.execute("UPDATE diagnostic_jobs SET snapshot=?", (json.dumps(snapshot),))
        repo = RunRepository(":memory:")
        workers = DiagnosticJobs(store, DiagnosticRuntime(repo, KnowledgeBase()), workers=1)
        workers.start()
        try:
            done = await workers.wait(job["job_id"], timeout=2)
            assert done["status"] == "failed", "unsupported snapshot ran as the current workflow"
        finally:
            await workers.close()
            repo.close()
            store.close()
    asyncio.run(scenario())
