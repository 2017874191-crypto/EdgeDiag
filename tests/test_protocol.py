from protocol.models import Telemetry
from protocol.topics import Topics


def test_telemetry_contract_and_topic():
    message = Telemetry(
        device_id="motor-001", boot_id="boot-1", seq=1, timestamp_ms=1,
        firmware_version="0.1.0", metrics={"temperature_c": 42.0},
    )
    assert message.device_id == "motor-001"
    assert Topics.telemetry("motor-001") == "device/motor-001/telemetry"


def test_extra_fields_are_rejected():
    try:
        Telemetry(
            device_id="motor-001", boot_id="boot-1", seq=1, timestamp_ms=1,
            firmware_version="0.1.0", metrics={"temperature_c": 42.0}, unknown=1,
        )
    except ValueError:
        return
    raise AssertionError("extra fields must be rejected")
