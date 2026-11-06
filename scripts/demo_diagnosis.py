"""Run against a live API: python -m scripts.demo_diagnosis."""
import json
import os
import time
import uuid
import httpx


def main():
    with httpx.Client(base_url=os.getenv("EDGEDIAG_API_URL", "http://localhost:8000"), timeout=90) as client:
        response = client.post("/v1/telemetry", json={
            "device_id": "motor-demo", "boot_id": uuid.uuid4().hex, "seq": 1,
            "timestamp_ms": int(time.time()*1000), "firmware_version": "sim-0.1.0",
            "metrics": {"temperature_c": 85, "vibration_rms": 1.8, "current_a": 2.2},
        })
        response.raise_for_status()
        incident = response.json()["incidents"][-1]
        response = client.post(f'/v1/incidents/{incident["incident_id"]}/runs')
        response.raise_for_status()
        run = response.json()
        saved = client.get(f'/v1/runs/{run["trace_id"]}')
        saved.raise_for_status()
        assert saved.json() == run
        print(json.dumps(run, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
