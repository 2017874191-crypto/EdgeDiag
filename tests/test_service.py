from fastapi.testclient import TestClient
from services.app import create_app


def message(seq=1, timestamp=1000, boot="boot-test", temperature=85, vibration=1.8):
    return {"device_id": "motor-test", "boot_id": boot, "seq": seq,
            "timestamp_ms": timestamp, "firmware_version": "sim-0.1.0",
            "metrics": {"temperature_c": temperature, "vibration_rms": vibration}}


def trigger(client):
    for n in range(1, 4):
        response = client.post("/v1/telemetry", json=message(n, n*1000))
        assert response.status_code == 202
    return response.json()["incidents"][0]["incident_id"]


def test_ingest_deduplicates_and_detects_sustained_anomaly():
    with TestClient(create_app()) as client:
        iid = trigger(client)
        second = client.post("/v1/telemetry", json=message(3, 3000))
        assert second.json()["accepted"] is False
        assert client.get("/v1/incidents/" + iid).json()["type"] == "thermal_vibration"
        assert len(client.get("/v1/incidents").json()["items"]) == 1


def test_health():
    with TestClient(create_app()) as client:
        assert client.get("/health").json()["status"] == "ok"
