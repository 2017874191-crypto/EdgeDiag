"""Exercise the live HTTP persistence and idempotent diagnosis flow."""
import json
import os
import time
import uuid
import httpx


def main():
    with httpx.Client(base_url=os.getenv("EDGEDIAG_API_URL", "http://localhost:8000"), timeout=90) as client:
        boot = uuid.uuid4().hex
        now = int(time.time()*1000)
        for seq in range(1, 4):
            response = client.post("/v1/telemetry", json={
                "device_id": "motor-demo", "boot_id": boot, "seq": seq,
                "timestamp_ms": now - (3-seq)*1000, "firmware_version": "sim-0.2.0",
                "metrics": {"temperature_c": 85, "vibration_rms": 1.8, "current_a": 2.2},
            })
            response.raise_for_status()
        incident = response.json()["incidents"][-1]
        key = {"request_key": uuid.uuid4().hex, "expected_revision": incident["revision"]}
        path = f'/v1/incidents/{incident["incident_id"]}/runs'
        response = client.post(path, json=key)
        response.raise_for_status()
        run = response.json()
        replay = client.post(path, json=key)
        replay.raise_for_status()
        assert replay.json() == run
        saved = client.get(f'/v1/runs/{run["trace_id"]}')
        saved.raise_for_status()
        assert saved.json() == run
        print(json.dumps({"incident_id": incident["incident_id"], "trace_id": run["trace_id"],
                          "status": run["status"], "diagnosis_mode": run["diagnosis"]["mode"],
                          "tools": len(run["events"]), "idempotent_replay": True,
                          "persisted_trace_matches": True}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
