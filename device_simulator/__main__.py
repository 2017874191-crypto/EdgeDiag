from __future__ import annotations

import argparse
import json
import math
import random
import time
import uuid
import paho.mqtt.client as mqtt
from protocol.models import Telemetry
from protocol.topics import Topics


def build_message(device_id: str, boot_id: str, seq: int, scenario: str) -> Telemetry:
    phase = seq / 10
    temperature = 55 + math.sin(phase / 3) * 2
    vibration = 0.45 + abs(math.sin(phase)) * 0.08
    current = 2.0 + abs(math.sin(phase / 2)) * 0.2
    quality = {"sensor_valid": True, "dropped_samples": 0}
    if scenario == "thermal_runaway":
        temperature += max(0, seq - 5) * 3.0
    elif scenario == "bearing_fault":
        temperature += max(0, seq - 5) * 1.8
        vibration += max(0, seq - 5) * 0.12
    elif scenario == "sensor_invalid" and seq >= 5:
        quality["sensor_valid"] = False
    return Telemetry(
        device_id=device_id, boot_id=boot_id, seq=seq,
        timestamp_ms=int(time.time() * 1000), firmware_version="sim-0.1.0",
        metrics={"temperature_c": round(temperature, 3), "vibration_rms": round(vibration, 3), "current_a": round(current, 3)},
        quality=quality,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="motor-001")
    parser.add_argument("--scenario", choices=["normal", "thermal_runaway", "bearing_fault", "sensor_invalid", "offline"], default="normal")
    parser.add_argument("--duration", type=int, default=30)
    parser.add_argument("--broker", default="localhost")
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"sim-{args.device}")
    client.connect(args.broker, 1883, 60)
    client.loop_start()
    boot_id = f"boot-{uuid.uuid4().hex[:8]}"
    try:
        for seq in range(args.duration):
            if args.scenario != "offline":
                message = build_message(args.device, boot_id, seq, args.scenario)
                client.publish(Topics.telemetry(args.device), message.model_dump_json(), qos=1)
                print(json.dumps(message.model_dump(mode="json"), ensure_ascii=False))
            time.sleep(args.interval)
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
