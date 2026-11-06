from __future__ import annotations

import uuid
from protocol.models import Incident, Telemetry


def detect(message: Telemetry, history: list[Telemetry]) -> list[Incident]:
    metrics = message.metrics
    result: list[Incident] = []
    temperature = metrics.get("temperature_c")
    vibration = metrics.get("vibration_rms")
    if not message.quality.sensor_valid:
        result.append(Incident(
            incident_id=f"inc-{uuid.uuid4().hex[:12]}", device_id=message.device_id,
            type="sensor_invalid", severity="medium", confidence=0.99,
            evidence={"quality": message.quality.model_dump()},
        ))
        return result
    if temperature is not None and temperature >= 80:
        result.append(Incident(
            incident_id=f"inc-{uuid.uuid4().hex[:12]}", device_id=message.device_id,
            type="thermal", severity="high", confidence=0.90,
            evidence={"temperature_c": temperature, "threshold_c": 80},
        ))
    if vibration is not None and vibration >= 1.5:
        result.append(Incident(
            incident_id=f"inc-{uuid.uuid4().hex[:12]}", device_id=message.device_id,
            type="vibration", severity="high", confidence=0.88,
            evidence={"vibration_rms": vibration, "threshold": 1.5},
        ))
    if temperature is not None and vibration is not None and temperature >= 75 and vibration >= 1.2:
        result.append(Incident(
            incident_id=f"inc-{uuid.uuid4().hex[:12]}", device_id=message.device_id,
            type="thermal_vibration", severity="high", confidence=0.93,
            evidence={"temperature_c": temperature, "vibration_rms": vibration},
        ))
    return result
