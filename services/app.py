from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from protocol.models import Diagnosis, Telemetry
from protocol.topics import Topics
from services.anomaly import detect
from services.state import StateStore
from services.mqtt_consumer import TelemetryConsumer

store = StateStore()
consumer = TelemetryConsumer(store)


def build_diagnosis(incident_id: str) -> Diagnosis:
    incident = store.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="incident not found")
    mapping = {
        "thermal": "疑似过热，请检查散热和负载",
        "vibration": "疑似机械振动异常，请检查轴承、紧固件和偏心",
        "thermal_vibration": "温度和振动同时异常，优先检查轴承润滑和机械偏心",
        "sensor_invalid": "传感器数据无效，请检查传感器连接和供电",
        "offline": "设备未按预期上报数据，请检查网络和设备电源",
    }
    history = store.history(incident.device_id)
    evidence = [incident.evidence, {"history_samples": len(history)}]
    return Diagnosis(
        incident_id=incident.incident_id,
        device_id=incident.device_id,
        status="diagnosed",
        severity=incident.severity,
        summary=mapping[incident.type],
        confidence=incident.confidence,
        evidence=evidence,
        hypotheses=[{"cause": mapping[incident.type], "confidence": incident.confidence}],
        recommendations=[{"action": mapping[incident.type], "risk": "low", "requires_confirmation": False}],
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    consumer.start()
    yield
    consumer.stop()


app = FastAPI(title="EdgeDiag", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok", "service": "edgediag", "version": "0.1.0"}


@app.post("/v1/telemetry", status_code=202)
def ingest(message: Telemetry):
    accepted = store.accept(message)
    if not accepted:
        return {"accepted": False, "reason": "duplicate", "device_id": message.device_id, "seq": message.seq}
    incidents = detect(message, store.history(message.device_id))
    for incident in incidents:
        store.add_incident(incident)
    return {
        "accepted": True,
        "device_id": message.device_id,
        "seq": message.seq,
        "incidents": [incident.model_dump(mode="json") for incident in incidents],
    }


@app.get("/v1/devices/{device_id}/telemetry")
def telemetry(device_id: str, limit: int = 100):
    return {"device_id": device_id, "items": [item.model_dump(mode="json") for item in store.history(device_id, min(limit, 300))]}


@app.get("/v1/incidents/{incident_id}")
def incident(incident_id: str):
    item = store.get_incident(incident_id)
    if item is None:
        raise HTTPException(status_code=404, detail="incident not found")
    return item


@app.post("/v1/incidents/{incident_id}/diagnose", response_model=Diagnosis)
def diagnose(incident_id: str):
    return build_diagnosis(incident_id)
