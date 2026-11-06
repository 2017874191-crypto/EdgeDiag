"""Event-time rules: three consecutive samples spanning >=2 seconds, gaps <=5s."""
import uuid
from protocol.models import Incident, Telemetry

WINDOW_SAMPLES = 3
MIN_DURATION_MS = 2000
MAX_GAP_MS = 5000


def classify(message: Telemetry) -> str | None:
    if not message.quality.sensor_valid:
        return "sensor_invalid"
    temperature = message.metrics.get("temperature_c")
    vibration = message.metrics.get("vibration_rms")
    if temperature is None or vibration is None:
        return "insufficient_data"
    if temperature is not None and vibration is not None and temperature >= 75 and vibration >= 1.2:
        return "thermal_vibration"
    if temperature is not None and temperature >= 80:
        return "thermal"
    if vibration is not None and vibration >= 1.5:
        return "vibration"
    return None


def stable_type(message, history):
    """None: insufficient evidence; ('normal',): sustained recovery."""
    if not message.quality.sensor_valid:
        return ("sensor_invalid",)
    recent = [m for m in history if m.boot_id == message.boot_id
              and m.timestamp_ms <= message.timestamp_ms][-WINDOW_SAMPLES:]
    if len(recent) < WINDOW_SAMPLES:
        return None
    times = [m.timestamp_ms for m in recent]
    gaps = [b-a for a, b in zip(times, times[1:])]
    if times[-1] - times[0] < MIN_DURATION_MS or not all(0 < g <= MAX_GAP_MS for g in gaps):
        return None
    kinds = [classify(m) for m in recent]
    if "insufficient_data" in kinds:
        return None
    if len(set(kinds)) != 1:
        return None
    return (kinds[0] or "normal",)


def detect(message: Telemetry, history: list[Telemetry]) -> list[Incident]:
    stable = stable_type(message, history)
    if stable is None or stable == ("normal",):
        return []
    kind = stable[0]
    return [Incident(
        incident_id=f"inc-{uuid.uuid4().hex[:12]}", device_id=message.device_id,
        type=kind, severity="medium" if kind == "sensor_invalid" else "high",
        detector="sustained-rule-v2", confidence=0,
        evidence={"metrics": message.metrics, "quality": message.quality.model_dump(),
                  "boot_id": message.boot_id, "seq": message.seq,
                  "timestamp_ms": message.timestamp_ms, "window_samples": 1 if kind == "sensor_invalid" else 3},
    )]
