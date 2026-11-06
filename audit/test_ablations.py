"""Paired test-only ablations. No production file or persistent user database is modified."""
import asyncio
from contextlib import contextmanager, nullcontext
import inspect
import sqlite3
import textwrap
from unittest.mock import patch
from pydantic import Field
from agent.registry import Arguments, ToolRegistry, ToolSpec, ToolContext
from agent.runtime import DiagnosticRuntime
from agent.repository import RunRepository
from agent.knowledge import KnowledgeBase
from protocol.models import Telemetry
from services.state import StateStore
import services.anomaly as anomaly
from tests.test_service import message
from tests.test_agent import incident, sample, FakeProvider, final

RESULTS = []


def record(mechanism, full, ablated, claim):
    RESULTS.append({"mechanism": mechanism, "full": full, "ablated": ablated, "claim": claim})


@contextmanager
def mutate_method(cls, method, old, new):
    # Single in-memory mutation; fail explicitly if the implementation no longer matches.
    source = textwrap.dedent(inspect.getsource(getattr(cls, method)))
    assert source.count(old) == 1, "ablation mutation needs review after implementation changes"
    namespace = dict(vars(inspect.getmodule(cls)))
    exec(compile(source.replace(old, new), "<test-only-ablation>", "exec"), namespace)
    with patch.object(cls, method, namespace[method]):
        yield


def seed(store):
    for n in range(1, 4):
        result = store.ingest(Telemetry.model_validate(message(n, n*1000)))
    return result["incidents"][0].incident_id


def test_transaction_ablation():
    def experiment(atomic):
        store = StateStore()
        for n in (1, 2):
            store.ingest(Telemetry.model_validate(message(n, n*1000)))
        @contextmanager
        def nonatomic():
            yield store.db  # isolation_level=None: statements commit individually.
        def fail_event(*args):
            raise RuntimeError("injected after incident write")
        try:
            with patch.object(store, "_event", fail_event):
                context = patch.object(store, "transaction", nonatomic) if not atomic else nullcontext()
                with context:
                    try:
                        store.ingest(Telemetry.model_validate(message(3, 3000)))
                    except RuntimeError:
                        pass
            return {"samples": len(store.history("motor-test")), "incidents": len(store.list_incidents())}
        finally:
            store.close()
    full, off = experiment(True), experiment(False)
    assert full == {"samples": 2, "incidents": 0}
    assert off == {"samples": 3, "incidents": 1}
    record("atomic_ingestion", full, off, "failure after incident write does not partially commit")


def test_window_ablation():
    def experiment():
        store = StateStore()
        try:
            cases = [(50,.4), (85,1.8), (50,.4)]
            for n, (t,v) in enumerate(cases, 1):
                store.ingest(Telemetry.model_validate(message(n,n*1000,temperature=t,vibration=v)))
            return len(store.list_incidents())
        finally:
            store.close()
    full = experiment()
    with patch.object(anomaly, "WINDOW_SAMPLES", 1), patch.object(anomaly, "MIN_DURATION_MS", 0):
        off = experiment()
    assert (full, off) == (0, 1)
    record("sustained_window_spike", full, off, "isolated spike creates no incident; not a measured field false-alarm rate")


def test_window_detection_tradeoff():
    def experiment():
        store = StateStore()
        try:
            for n in range(1,4):
                result = store.ingest(Telemetry.model_validate(message(n,n*1000)))
                if result["incidents"]:
                    return n
        finally:
            store.close()
    full = experiment()
    with patch.object(anomaly, "WINDOW_SAMPLES", 1), patch.object(anomaly, "MIN_DURATION_MS", 0):
        off = experiment()
    assert (full, off) == (3, 1)
    record("sustained_window_delay", full, off, "spike rejection costs two samples of latency on this fixed trace")


def test_gap_guard_ablation():
    def experiment():
        store = StateStore()
        try:
            for n,t in enumerate([1000,20000,40000],1):
                store.ingest(Telemetry.model_validate(message(n,t)))
            return len(store.list_incidents())
        finally:
            store.close()
    full = experiment()
    with patch.object(anomaly, "MAX_GAP_MS", 10**9):
        off = experiment()
    assert (full, off) == (0, 1)
    record("maximum_sample_gap", full, off, "widely separated samples are not called sustained")


def test_request_replay_ablation():
    def experiment(dedup):
        store = StateStore()
        try:
            iid = seed(store)
            jobs = [store.submit_job(iid, "same" if dedup else f"fresh-{n}")[0]["job_id"] for n in range(2)]
            return len(set(jobs))
        finally:
            store.close()
    full, off = experiment(True), experiment(False)
    assert (full, off) == (1, 2)
    record("request_receipt", full, off, "two retry attempts share a job; ablation intentionally removes stable request identity")


def test_device_scope_ablation():
    class Args(Arguments):
        device_id: str = Field(min_length=1)
    async def experiment():
        registry = ToolRegistry()
        async def lookup(ctx, args):
            return {"device": args.device_id, "demo_private_value": 42}
        registry.register(ToolSpec("lookup", "", Args, lookup))
        result = await registry.execute("lookup", {"device_id":"other"}, ToolContext("mine","incident"), [])
        return result["ok"]
    full = asyncio.run(experiment())
    old = 'if hasattr(args, "device_id") and args.device_id != context.device_id:'
    with mutate_method(ToolRegistry, "execute", old, "if False:"):
        off = asyncio.run(experiment())
    assert (full, off) == (False, True)
    record("device_scope", full, off, "cross-device arguments cannot reach this scoped handler")


def test_tool_timeout_ablation():
    async def experiment(timeout):
        registry = ToolRegistry()
        async def slow(ctx, args):
            await asyncio.sleep(.03)
            return "done"
        registry.register(ToolSpec("slow", "", Arguments, slow, timeout_s=timeout, retry_safe=False))
        return (await registry.execute("slow", {}, ToolContext("d","i"), []))["ok"]
    full, off = asyncio.run(experiment(.001)), asyncio.run(experiment(1))
    assert (full, off) == (False, True)
    record("tool_deadline", full, off, "slow handler is cut off; test-only ablation retains a one-second safety cap")


def test_citation_check_ablation():
    def experiment():
        repo = RunRepository(":memory:")
        try:
            rt = DiagnosticRuntime(repo, KnowledgeBase(), FakeProvider([final(["invented"])]*6))
            return asyncio.run(rt.run(incident(), [sample()]))["diagnosis"]["mode"]
        finally:
            repo.close()
    full = experiment()
    old = 'if not set(finding.evidence_ids) <= known_evidence or not set(finding.document_ids) <= known_docs:'
    with mutate_method(DiagnosticRuntime, "_execute", old, "if False:"):
        off = experiment()
    assert (full, off) == ("rules", "agent")
    record("citation_membership", full, off, "unknown evidence IDs do not become accepted reports; no semantic grounding claim")


def test_unique_constraint_sequential_negative_control(tmp_path):
    def experiment(unique):
        path = tmp_path / ("unique.db" if unique else "without-unique.db")
        if not unique:
            db = sqlite3.connect(path)
            db.execute("""CREATE TABLE telemetry(id INTEGER PRIMARY KEY,device_id TEXT NOT NULL,
                          boot_id TEXT NOT NULL,seq INTEGER NOT NULL,timestamp_ms INTEGER NOT NULL,body TEXT NOT NULL)""")
            db.close()
        store = StateStore(path)
        try:
            msg = Telemetry.model_validate(message())
            store.ingest(msg)
            store.ingest(msg)
            sequential = len(store.history("motor-test"))
            try:
                store.db.execute("INSERT INTO telemetry(device_id,boot_id,seq,timestamp_ms,body) VALUES(?,?,?,?,?)",
                                 (msg.device_id,msg.boot_id,msg.seq,msg.timestamp_ms,msg.model_dump_json()))
            except sqlite3.IntegrityError:
                pass
            return {"normal_path_rows": sequential, "after_bypass_writer": len(store.history("motor-test"))}
        finally:
            store.close()
    full, off = experiment(True), experiment(False)
    assert full == {"normal_path_rows":1,"after_bypass_writer":1}
    assert off == {"normal_path_rows":1,"after_bypass_writer":2}
    record("database_unique_constraint", full, off,
           "negative control: sequential API dedup alone has no difference; constraint protects bypass writers")


def test_frozen_snapshot_ablation():
    def experiment(frozen):
        store = StateStore()
        repo = RunRepository(":memory:")
        try:
            iid = seed(store)
            store.submit_job(iid)
            store.ingest(Telemetry.model_validate(message(4,4000)))
            _, snapshot = store.claim_job()
            from protocol.models import Incident
            history = ([Telemetry.model_validate(m) for m in snapshot["history"]] if frozen
                       else store.history("motor-test"))
            rt = DiagnosticRuntime(repo, KnowledgeBase())
            run = asyncio.run(rt.run(Incident.model_validate(snapshot["incident"]), history))
            return next(e["data"]["sample_count"] for e in run["diagnosis"]["evidence"]
                        if e["source"] == "get_device_status")
        finally:
            repo.close()
            store.close()
    full, off = experiment(True), experiment(False)
    assert (full, off) == (3, 4)
    record("frozen_telemetry_snapshot", full, off, "later samples cannot change a submitted task's telemetry context")


def test_capacity_ablation():
    def experiment(capacity):
        store = StateStore(capacity=capacity)
        try:
            iid = seed(store)
            from services.state import CapacityError
            accepted = 0
            for n in range(2):
                try:
                    store.submit_job(iid, f"r-{n}")
                    accepted += 1
                except CapacityError:
                    pass
            return accepted
        finally:
            store.close()
    full, off = experiment(1), experiment(1000)
    assert (full, off) == (1, 2)
    record("pending_capacity", full, off, "admission stays within fixture capacity; no throughput benchmark")


def test_recovery_ablation():
    def experiment(recover):
        store = StateStore()
        try:
            iid = seed(store)
            store.submit_job(iid)
            store.claim_job()  # An owner disappeared while running.
            if recover:
                store.recover_jobs()
            return store.claim_job() is not None
        finally:
            store.close()
    full, off = experiment(True), experiment(False)
    assert (full, off) == (True, False)
    record("interrupted_job_requeue", full, off, "single-owner restart can claim abandoned read-only work")


def test_rules_mode_manual_lookup_negative_control():
    def experiment():
        repo = RunRepository(":memory:")
        try:
            run = asyncio.run(DiagnosticRuntime(repo, KnowledgeBase()).run(incident(),[sample()]))
            return {"summary":run["diagnosis"]["summary"],
                    "tool_calls":len(run["events"]), "mode":run["diagnosis"]["mode"]}
        finally:
            repo.close()
    full = experiment()
    old = 'if self.provider is not None:'
    with mutate_method(DiagnosticRuntime, "_execute", old, "if False:"):
        off = experiment()
    assert full["summary"] == off["summary"] and full["mode"] == off["mode"] == "rules"
    assert (full["tool_calls"], off["tool_calls"]) == (3,3)
    record("rules_manual_lookup_negative_control", full, off,
           "post-fix rules path already skips retrieval: removing the optional branch has no further effect")


def test_worker_database_retry_ablation():
    from services.jobs import DiagnosticJobs
    async def experiment():
        store=StateStore()
        iid=seed(store)
        job,_=store.submit_job(iid)
        claim=store.claim_job
        count=0
        def flaky():
            nonlocal count
            count+=1
            if count==1:
                raise sqlite3.OperationalError("database is locked")
            return claim()
        async def run(*args):
            return {"trace_id":"once"}
        from types import SimpleNamespace
        workers=DiagnosticJobs(store,SimpleNamespace(run=run),workers=1,retry_delay=.001)
        with patch.object(store,"claim_job",flaky):
            workers.start()
            try:
                for _ in range(100):
                    if workers.tasks[0].done() or store.get_job(job["job_id"])["status"]=="completed":
                        break
                    await asyncio.sleep(.002)
                return {"status":store.get_job(job["job_id"])["status"],
                        "alive":workers.health()["alive_workers"]}
            finally:
                await workers.close()
                store.close()
    full=asyncio.run(experiment())
    old="if not transient_database_error(error) or attempt == 4:"
    with mutate_method(DiagnosticJobs,"_database",old,"if True:"):
        off=asyncio.run(experiment())
    assert full=={"status":"completed","alive":1}
    assert off=={"status":"queued","alive":0}
    record("transient_claim_retry",full,off,"one transient failure recovers; removing retry strands work")


def test_future_clock_guard_ablation():
    import services.state as state
    def experiment():
        store=StateStore(clock_ms=lambda:100000)
        try:
            store.ingest(Telemetry.model_validate(message(1,10**15,boot="bad",temperature=50,vibration=.4)))
            for n in range(1,4):
                store.ingest(Telemetry.model_validate(message(n,n*1000,boot="correct")))
            return len(store.list_incidents())
        finally:
            store.close()
    full=experiment()
    with patch.object(state,"MAX_FUTURE_SKEW_MS",10**18):
        off=experiment()
    assert (full,off)==(1,0)
    record("future_clock_quarantine",full,off,"quarantine prevents a future packet suppressing corrected-clock anomalies")


def test_workflow_gate_ablation():
    import json
    from services.jobs import DiagnosticJobs
    async def experiment():
        store=StateStore()
        iid=seed(store)
        job,_=store.submit_job(iid)
        snap=json.loads(store.db.execute("SELECT snapshot FROM diagnostic_jobs").fetchone()["snapshot"])
        snap["workflow_version"]="unknown"
        store.db.execute("UPDATE diagnostic_jobs SET snapshot=?",(json.dumps(snap),))
        calls=[]
        async def run(*args):
            calls.append(1)
            return {"trace_id":"unexpected"}
        from types import SimpleNamespace
        workers=DiagnosticJobs(store,SimpleNamespace(run=run),workers=1)
        workers.start()
        try:
            done=await workers.wait(job["job_id"],timeout=1)
            return {"status":done["status"],"runtime_calls":len(calls)}
        finally:
            await workers.close()
            store.close()
    full=asyncio.run(experiment())
    with mutate_method(DiagnosticJobs,"_worker",
                       'if snapshot.get("workflow_version") != WORKFLOW_VERSION:',"if False:"):
        off=asyncio.run(experiment())
    assert full=={"status":"failed","runtime_calls":0}
    assert off=={"status":"completed","runtime_calls":1}
    record("workflow_version_gate",full,off,"unsupported snapshots are rejected before runtime invocation")
