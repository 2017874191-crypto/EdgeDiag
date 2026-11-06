"""Failure/recovery, migration and version semantics added with the review fixes."""
import asyncio
import json
import sqlite3
from unittest.mock import AsyncMock
import pytest
from fastapi.testclient import TestClient
from agent.contracts import WORKFLOW_VERSION
from protocol.models import Telemetry
from services.app import create_app
from services.jobs import DiagnosticJobs
from services.state import StateStore, MAX_FUTURE_SKEW_MS
from tests.test_service import message
from tests.test_persistence import seed


def test_legacy_future_watermark_migration_is_additive_and_repeatable(tmp_path):
    path = tmp_path / "legacy.db"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE telemetry(id INTEGER PRIMARY KEY,device_id TEXT NOT NULL,boot_id TEXT NOT NULL,
          seq INTEGER NOT NULL,timestamp_ms INTEGER NOT NULL,body TEXT NOT NULL,UNIQUE(device_id,boot_id,seq));
        CREATE TABLE devices(device_id TEXT PRIMARY KEY,watermark_ms INTEGER NOT NULL,received_ms INTEGER NOT NULL);
    """)
    bad = Telemetry.model_validate(message(1,10**15,boot="bad"))
    db.execute("INSERT INTO telemetry(device_id,boot_id,seq,timestamp_ms,body) VALUES(?,?,?,?,?)",
               (bad.device_id,bad.boot_id,bad.seq,bad.timestamp_ms,bad.model_dump_json()))
    db.execute("INSERT INTO devices VALUES(?,?,?)", (bad.device_id,bad.timestamp_ms,100000))
    db.commit()
    db.close()
    for _ in range(2):
        store = StateStore(path,clock_ms=lambda:100000)
        assert store.last_seen["motor-test"] <= 3000
        assert len(store.quarantined("motor-test")) == 1
        assert not store.ingest(bad)["accepted"]  # Quarantined packet remains deduplicated.
        for n in range(1,4):
            store.ingest(Telemetry.model_validate(message(n,n*1000,boot="correct")))
        assert len(store.history("motor-test")) == 3
        assert len(store.list_incidents()) == 1
        store.close()


def test_future_boundary_and_quarantine_does_not_enter_snapshot():
    store = StateStore(clock_ms=lambda:100000)
    iid = seed(store)
    future = Telemetry.model_validate(message(99,100000 + MAX_FUTURE_SKEW_MS + 1))
    assert store.ingest(future)["reason"] == "clock_quarantined"
    store.submit_job(iid)
    _, snap = store.claim_job()
    assert len(snap["history"]) == 3
    assert len(store.quarantined("motor-test")) == 1
    assert store.quarantined("motor-test")[0]["checked_ms"] == 100000
    assert store.last_seen["motor-test"] == 3000
    boundary = Telemetry.model_validate(message(100,100000+MAX_FUTURE_SKEW_MS))
    assert store.ingest(boundary)["reason"] == "accepted"
    store.close()


def test_quarantine_api_and_old_boot_replay_are_explicit():
    with TestClient(create_app()) as client:
        bad = message(1,10**15,boot="bad")
        assert client.post("/v1/telemetry",json=bad).json()["reason"] == "clock_quarantined"
        for n in range(1,4):
            client.post("/v1/telemetry",json=message(n,n*1000,boot="current"))
        old = message(2,500,boot="old",temperature=20,vibration=.1)
        assert client.post("/v1/telemetry",json=old).json()["reason"] == "late"
        assert len(client.get("/v1/devices/motor-test/quarantine").json()["items"]) == 1
        assert client.get("/v1/incidents").json()["items"][0]["status"] == "open"


def test_completion_retry_does_not_repeat_runtime(monkeypatch):
    async def scenario():
        store = StateStore()
        iid = seed(store)
        job,_ = store.submit_job(iid)
        runtime = type("Runtime",(),{"run":AsyncMock(return_value={"trace_id":"trace-once"})})()
        finish = store.finish_job
        calls = 0
        def flaky(*args,**kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise sqlite3.OperationalError("database is locked")
            return finish(*args,**kwargs)
        monkeypatch.setattr(store,"finish_job",flaky)
        workers = DiagnosticJobs(store,runtime,workers=1,retry_delay=.001)
        workers.start()
        try:
            done = await workers.wait(job["job_id"],timeout=1)
            assert done["status"] == "completed"
            assert calls == 2 and runtime.run.await_count == 1
        finally:
            await workers.close()
            store.close()
    asyncio.run(scenario())


def test_persistent_contention_stops_after_five_attempts_and_reports_unready(monkeypatch):
    async def scenario():
        store = StateStore()
        runtime = type("Runtime",(),{"run":AsyncMock()})()
        calls = 0
        def blocked():
            nonlocal calls
            calls += 1
            raise sqlite3.OperationalError("database is locked")
        monkeypatch.setattr(store,"claim_job",blocked)
        workers = DiagnosticJobs(store,runtime,workers=1,retry_delay=.001)
        workers.start()
        try:
            await asyncio.wait_for(workers.tasks[0],timeout=1)
            assert calls == 5
            assert workers.health()["status"] == "degraded"
            assert workers.health()["alive_workers"] == 0
            assert workers.health()["worker_errors"][0] == "database_unavailable"
            assert runtime.run.await_count == 0
        finally:
            await workers.close()
            store.close()
    asyncio.run(scenario())


def test_cancellation_during_backoff_is_not_swallowed(monkeypatch):
    async def scenario():
        store = StateStore()
        entered = asyncio.Event()
        def blocked():
            entered.set()
            raise sqlite3.OperationalError("database is locked")
        monkeypatch.setattr(store,"claim_job",blocked)
        workers = DiagnosticJobs(store,None,workers=1,retry_delay=1)
        workers.start()
        await entered.wait()
        await asyncio.wait_for(workers.close(),timeout=.5)
        assert not workers.tasks
        store.close()
    asyncio.run(scenario())


def test_fatal_database_error_is_not_retried(monkeypatch):
    async def scenario():
        store = StateStore()
        calls=0
        def broken():
            nonlocal calls
            calls += 1
            raise sqlite3.OperationalError("disk I/O error")
        monkeypatch.setattr(store,"claim_job",broken)
        workers = DiagnosticJobs(store,None,workers=1,retry_delay=.001)
        workers.start()
        await asyncio.wait_for(workers.tasks[0],timeout=1)
        assert calls == 1 and workers.health()["status"] == "degraded"
        await workers.close()
        store.close()
    asyncio.run(scenario())


def test_http_health_reflects_lost_worker():
    app=create_app()
    with TestClient(app) as client:
        workers=app.state.jobs
        async def kill():
            workers.tasks[0].cancel()
            await asyncio.gather(workers.tasks[0],return_exceptions=True)
        client.portal.call(kill)
        response=client.get("/health")
        assert response.status_code == 503
        assert response.json()["alive_workers"] == 1


@pytest.mark.parametrize("version",[None,"diagnose-v1","unknown"])
def test_version_gate_never_calls_runtime_for_unsupported_snapshots(version):
    async def scenario():
        store=StateStore()
        iid=seed(store)
        job,_=store.submit_job(iid)
        snap=json.loads(store.db.execute("SELECT snapshot FROM diagnostic_jobs").fetchone()["snapshot"])
        if version is None:
            snap.pop("workflow_version")
        else:
            snap["workflow_version"]=version
        store.db.execute("UPDATE diagnostic_jobs SET snapshot=?",(json.dumps(snap),))
        runtime=type("Runtime",(),{"run":AsyncMock()})()
        workers=DiagnosticJobs(store,runtime,workers=1)
        workers.start()
        try:
            done=await workers.wait(job["job_id"],timeout=1)
            assert done["error"] == "unsupported_workflow_version"
            assert runtime.run.await_count == 0
        finally:
            await workers.close()
            store.close()
    asyncio.run(scenario())


def test_task_and_runtime_share_version_constant():
    from tests.test_agent import run_with
    store=StateStore()
    iid=seed(store)
    store.submit_job(iid)
    _,snapshot=store.claim_job()
    assert snapshot["workflow_version"] == run_with()["workflow_version"] == WORKFLOW_VERSION
    store.close()


def test_completion_failure_does_not_reexecute_model_in_same_attempt(monkeypatch):
    async def scenario():
        store=StateStore()
        iid=seed(store)
        job,_=store.submit_job(iid)
        runtime=type("Runtime",(),{"run":AsyncMock(return_value={"trace_id":"saved-separately"})})()
        calls=0
        def blocked(*args,**kwargs):
            nonlocal calls
            calls+=1
            raise sqlite3.OperationalError("database is locked")
        monkeypatch.setattr(store,"finish_job",blocked)
        workers=DiagnosticJobs(store,runtime,workers=1,retry_delay=.001)
        workers.start()
        try:
            await asyncio.wait_for(workers.tasks[0],timeout=1)
            assert calls == 5 and runtime.run.await_count == 1
            assert workers.health()["status"] == "degraded"
            assert store.get_job(job["job_id"])["status"] == "running"
        finally:
            await workers.close()
            store.close()
    asyncio.run(scenario())
