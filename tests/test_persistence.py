import asyncio
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from services.app import create_app
from services.state import StateStore, ConflictError, CapacityError
from services.jobs import DiagnosticJobs
from tests.test_service import message, trigger
from protocol.models import Telemetry
from agent.knowledge import KnowledgeBase
from agent.repository import RunRepository
from agent.runtime import DiagnosticRuntime


def seed(store, count=3):
    for n in range(1, count+1):
        result = store.ingest(Telemetry.model_validate(message(n, n*1000)))
    return result["incidents"][0].incident_id


def test_persistent_ingestion_dedup_and_recovery(tmp_path):
    path = tmp_path / "domain.db"
    store = StateStore(path)
    iid = seed(store, 5)
    assert len(store.list_incidents()) == 1
    assert store.get_incident(iid).occurrences == 3
    store.close()
    store = StateStore(path)
    assert len(store.history("motor-test")) == 5
    assert not store.ingest(Telemetry.model_validate(message(5, 5000)))["accepted"]
    assert store.get_incident(iid).occurrences == 3
    for n in range(6, 9):
        store.ingest(Telemetry.model_validate(message(n, n*1000, temperature=50, vibration=0.4)))
    assert store.get_incident(iid).status == "resolved"
    assert [e["kind"] for e in store.incident_events(iid)] == ["opened", "updated", "updated", "resolved"]
    for n in range(9, 12):
        latest = store.ingest(Telemetry.model_validate(message(n, n*1000)))
    assert latest["incidents"][0].incident_id != iid
    store.close()


def test_sequence_conflict_is_not_silent(tmp_path):
    store = StateStore(tmp_path / "db")
    store.ingest(Telemetry.model_validate(message()))
    with pytest.raises(ConflictError):
        store.ingest(Telemetry.model_validate(message(temperature=99)))
    assert len(store.history("motor-test")) == 1
    store.close()


def test_ingestion_transaction_rolls_back_all_effects(tmp_path, monkeypatch):
    store = StateStore(tmp_path / "db")
    seed_messages = [Telemetry.model_validate(message(n, n*1000)) for n in range(1, 4)]
    for m in seed_messages[:2]:
        store.ingest(m)
    def broken(*args):
        raise RuntimeError("injected persistence failure")
    with monkeypatch.context() as patch:
        patch.setattr(store, "_event", broken)
        with pytest.raises(RuntimeError):
            store.ingest(seed_messages[2])
    assert len(store.history("motor-test")) == 2
    assert not store.list_incidents()
    assert store.last_seen["motor-test"] == 2000
    assert store.ingest(seed_messages[2])["accepted"]
    store.close()


def test_concurrent_connections_create_one_incident_and_one_message(tmp_path):
    path = tmp_path / "db"
    stores = [StateStore(path), StateStore(path)]
    seed(stores[0], 3)
    msg = Telemetry.model_validate(message(4, 4000))
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda s: s.ingest(msg), stores))
    assert sum(r["accepted"] for r in results) == 1
    assert len(stores[0].list_incidents()) == 1
    assert len(stores[0].history("motor-test")) == 4
    for s in stores:
        s.close()


def test_late_data_cannot_resolve_current_incident():
    store = StateStore()
    iid = seed(store, 3)
    result = store.ingest(Telemetry.model_validate(message(99, 500, temperature=20, vibration=0.1)))
    assert result["reason"] == "late"
    assert store.get_incident(iid).status == "open"
    assert store.last_seen["motor-test"] == 3000


def test_transient_spike_and_gaps_do_not_open_incident():
    store = StateStore()
    for seq, ts, temp, vib in [(1,1000,50,.4),(2,2000,85,1.8),(3,3000,50,.4),
                                (4,20000,85,1.8),(5,21000,85,1.8)]:
        store.ingest(Telemetry.model_validate(message(seq,ts,temperature=temp,vibration=vib)))
    assert not store.list_incidents()


def test_reboot_cannot_complete_previous_boot_window():
    store = StateStore()
    for seq, ts, boot in [(1,1000,"a"),(2,2000,"a"),(1,3000,"b")]:
        store.ingest(Telemetry.model_validate(message(seq,ts,boot)))
    assert not store.list_incidents()


def test_invalid_sensor_does_not_resolve_physical_incident():
    store = StateStore()
    iid = seed(store)
    msg = message(4, 4000)
    msg["quality"] = {"sensor_valid": False}
    store.ingest(Telemetry.model_validate(msg))
    assert store.get_incident(iid).status == "open"
    assert len(store.list_incidents()) == 2


def test_missing_metrics_cannot_be_used_as_recovery_evidence():
    store = StateStore()
    iid = seed(store)
    for n in range(4, 7):
        msg = message(n, n*1000)
        msg["metrics"] = {"current_a": 1.0}
        store.ingest(Telemetry.model_validate(msg))
    assert store.get_incident(iid).status == "open"


def test_idempotency_receipt_binds_parameters_and_survives_restart(tmp_path):
    path = tmp_path / "db"
    store = StateStore(path)
    iid = seed(store)
    first, created = store.submit_job(iid, "retry-key")
    assert created
    seed(store, 4)
    repeated, created = store.submit_job(iid, "retry-key")
    assert not created and repeated == first
    with pytest.raises(ConflictError):
        store.submit_job(iid, "retry-key", expected_revision=999)
    store.close()
    store = StateStore(path)
    assert store.submit_job(iid, "retry-key")[0] == first
    store.close()


def test_queue_limit_replay_and_revision_conflicts():
    store = StateStore(capacity=1)
    iid = seed(store)
    first, _ = store.submit_job(iid)
    assert store.submit_job(iid)[0] == first
    with pytest.raises(CapacityError):
        store.submit_job(iid, "second")
    with pytest.raises(ConflictError):
        store.submit_job(iid, expected_revision=999)


def test_atomic_job_claim_between_connections(tmp_path):
    path = tmp_path / "db"
    a, b = StateStore(path), StateStore(path)
    iid = seed(a)
    a.submit_job(iid)
    with ThreadPoolExecutor(2) as pool:
        claims = list(pool.map(lambda s: s.claim_job(), [a,b]))
    assert sum(c is not None for c in claims) == 1
    a.close()
    b.close()


def test_worker_recovers_interrupted_read_only_job_with_frozen_snapshot(tmp_path):
    async def scenario():
        path = tmp_path / "db"
        store = StateStore(path)
        iid = seed(store)
        job, _ = store.submit_job(iid)
        store.claim_job()  # Simulate abrupt process loss.
        store.close()
        store = StateStore(path)
        seed(store, 4)
        repo = RunRepository(":memory:")
        worker = DiagnosticJobs(store, DiagnosticRuntime(repo, KnowledgeBase()), workers=1)
        worker.start()
        done = await worker.wait(job["job_id"], timeout=2)
        assert done["status"] == "completed" and done["attempts"] == 2
        evidence = done["result"]["diagnosis"]["evidence"]
        assert next(e for e in evidence if e["source"] == "get_device_status")["data"]["sample_count"] == 3
        await worker.close()
        repo.close()
        store.close()
    asyncio.run(scenario())


def test_recovery_attempt_limit_is_terminal():
    store = StateStore()
    iid = seed(store)
    job, _ = store.submit_job(iid)
    for _ in range(3):
        store.claim_job()
        store.recover_jobs()
    assert store.get_job(job["job_id"])["status"] == "failed"


def test_concurrent_api_retries_and_restart_replay():
    app = create_app()
    with TestClient(app) as client:
        iid = trigger(client)
        def request(_):
            r = client.post(f"/v1/incidents/{iid}/runs", json={"request_key": "same"})
            assert r.status_code == 200
            return r.json()
        with ThreadPoolExecutor(4) as pool:
            results = list(pool.map(request, range(4)))
        assert len({r["trace_id"] for r in results}) == 1
        assert client.post(f"/v1/incidents/{iid}/runs",
                           json={"request_key":"same","expected_revision":999}).status_code == 409
    with TestClient(create_app()) as client:
        replay = client.post(f"/v1/incidents/{iid}/runs", json={"request_key":"same"})
        assert replay.json() == results[0]
        assert client.get("/v1/incidents/" + iid).status_code == 200


def test_async_jobs_return_202_and_can_be_polled():
    with TestClient(create_app()) as client:
        iid = trigger(client)
        response = client.post(f"/v1/incidents/{iid}/jobs")
        assert response.status_code == 202
        job = response.json()
        assert client.get("/v1/jobs/" + job["job_id"]).status_code == 200
        assert client.get("/v1/jobs/missing").status_code == 404
        changed = message(3,3000,temperature=99)
        assert client.post("/v1/telemetry",json=changed).status_code == 409


def test_mqtt_ack_follows_commit_and_storage_failure_is_unacknowledged():
    from types import SimpleNamespace
    from unittest.mock import Mock
    from services.mqtt_consumer import TelemetryConsumer
    store = StateStore()
    consumer = TelemetryConsumer(store)
    client = Mock()
    def on_ack(mid, qos):
        assert len(store.history("motor-test")) == 1
    client.ack.side_effect = on_ack
    packet = SimpleNamespace(topic="device/motor-test/telemetry",
                             payload=Telemetry.model_validate(message()).model_dump_json().encode(),
                             mid=5, qos=1)
    consumer._on_message(client, None, packet)
    client.ack.assert_called_once_with(5, 1)
    client.ack.reset_mock()
    store.close()
    consumer._on_message(client, None, packet)
    client.ack.assert_not_called()


def test_cancelled_worker_returns_job_to_durable_queue():
    async def scenario():
        class Slow:
            async def run(self, *args):
                await asyncio.Event().wait()
        store = StateStore()
        iid = seed(store)
        job, _ = store.submit_job(iid)
        workers = DiagnosticJobs(store, Slow(), workers=1)
        workers.start()
        for _ in range(100):
            if store.get_job(job["job_id"])["status"] == "running":
                break
            await asyncio.sleep(0.001)
        assert store.get_job(job["job_id"])["status"] == "running"
        await workers.close()
        assert store.get_job(job["job_id"])["status"] == "queued"
        store.close()
    asyncio.run(scenario())
