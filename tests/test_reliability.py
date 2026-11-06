import pytest
from pydantic import ValidationError
from services.state import StateStore
from tests.test_agent import sample


def test_reboot_sequence_and_out_of_order_history():
    store = StateStore()
    assert store.accept(sample(seq=1, boot="a", timestamp=200))
    assert not store.accept(sample(seq=1, boot="a", timestamp=200))
    assert store.accept(sample(seq=1, boot="b", timestamp=300))
    assert store.accept(sample(seq=0, boot="a", timestamp=100))
    assert store.last_seen["motor-1"] == 300
    assert [m.timestamp_ms for m in store.history("motor-1")] == [100, 200, 300]


def test_invalid_sensor_does_not_trigger_physical_fault_diagnosis():
    from services.anomaly import detect
    msg = sample()
    msg.quality.sensor_valid = False
    assert [i.type for i in detect(msg, [msg])] == ["sensor_invalid"]


def test_mqtt_topic_must_match_device_identity():
    from types import SimpleNamespace
    from services.mqtt_consumer import TelemetryConsumer
    store = StateStore()
    consumer = TelemetryConsumer(store)
    consumer._on_message(None, None, SimpleNamespace(
        topic="device/other/telemetry", payload=sample().model_dump_json().encode()))
    assert store.history("motor-1") == []


@pytest.mark.parametrize("patch", [
    {"schema_version": 2}, {"device_id": "motor/+"},
    {"metrics": {"temperature_c": float("nan")}},
    {"metrics": {"temperature_c": float("inf")}},
])
def test_reject_invalid_protocol(patch):
    from protocol.models import Telemetry
    with pytest.raises(ValidationError):
        Telemetry.model_validate({**sample().model_dump(), **patch})
