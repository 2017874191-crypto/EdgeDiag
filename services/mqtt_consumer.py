from __future__ import annotations

import json
import logging
import os
import threading
import paho.mqtt.client as mqtt
from protocol.models import Telemetry
from services.anomaly import detect
from services.state import StateStore

logger = logging.getLogger(__name__)


class TelemetryConsumer:
    def __init__(self, store: StateStore):
        self.store = store
        self.host = os.getenv("MQTT_HOST", "localhost")
        self.port = int(os.getenv("MQTT_PORT", "1883"))
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="edgediag-ingestion")
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self._thread: threading.Thread | None = None

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            client.subscribe("device/+/telemetry", qos=1)
            logger.info("mqtt connected host=%s", self.host)
        else:
            logger.error("mqtt connection failed reason=%s", reason_code)

    def _on_message(self, client, userdata, message):
        try:
            telemetry = Telemetry.model_validate_json(message.payload)
            if self.store.accept(telemetry):
                for incident in detect(telemetry, self.store.history(telemetry.device_id)):
                    self.store.add_incident(incident)
        except Exception:
            logger.exception("invalid telemetry topic=%s", message.topic)

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
