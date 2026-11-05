from fastapi.testclient import TestClient
from services.app import app, store


def test_ingest_deduplicates_and_detects_anomaly():
    store.telemetry.clear()
    store.incidents.clear()
    store.seen.clear()
    message = {
        "device_id": "motor-test", "boot_id": "boot-test", "seq": 1,
        "timestamp_ms": 1, "firmware_version": "sim-0.1.0",
        "metrics": {"temperature_c": 85, "vibration_rms": 1.8},
    }
    with TestClient(app) as client:
        first = client.post("/v1/telemetry", json=message)
        second = client.post("/v1/telemetry", json=message)
    assert first.status_code == 202
    assert first.json()["accepted"] is True
    assert len(first.json()["incidents"]) == 3
    assert second.json()["accepted"] is False


def test_health():
    with TestClient(app) as client:
        assert client.get("/health").json()["status"] == "ok"
