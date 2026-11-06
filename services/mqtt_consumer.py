from __future__ import annotations

import logging
import os
import paho.mqtt.client as mqtt
from protocol.models import Telemetry
from services.state import StateStore

logger = logging.getLogger(__name__)


class TelemetryConsumer:
    def __init__(self, store: StateStore):
        self.store = store
        self.host = os.getenv("MQTT_HOST", "localhost")
        self.port = int(os.getenv("MQTT_PORT", "1883"))
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=os.getenv("MQTT_CLIENT_ID", "edgediag-ingestion"),
            clean_session=False, manual_ack=True)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            client.subscribe("device/+/telemetry", qos=1)
            logger.info("mqtt connected host=%s", self.host)
        else:
            logger.error("mqtt connection failed reason=%s", reason_code)

    def _on_message(self, client, userdata, message):
        try:
            telemetry = Telemetry.model_validate_json(message.payload)
            if message.topic != f"device/{telemetry.device_id}/telemetry":
                raise ValueError("topic/payload device mismatch")
            self.store.ingest(telemetry)
        except ValueError:
            # Malformed or conflicting packets are poison messages: discard and acknowledge.
            logger.warning("discarded invalid telemetry topic=%s", message.topic)
        except Exception:
            # Do not acknowledge transient storage failures. A persistent broker session
            # can redeliver unacknowledged QoS1 messages when the connection is restored.
            logger.exception("telemetry persistence failed topic=%s", message.topic)
            return
        if getattr(message, "qos", 0):
            client.ack(message.mid, message.qos)

    def start(self) -> None:
        if os.getenv("MQTT_ENABLED", "true").lower() not in {"1", "true", "yes"}:
            return
        self.client.connect_async(self.host, self.port, 60)
        self.client.loop_start()

    def stop(self) -> None:
        self.client.loop_stop()
        try:
            self.client.disconnect()
        except Exception:
            pass
