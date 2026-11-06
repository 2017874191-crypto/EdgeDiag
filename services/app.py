from __future__ import annotations

import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query, Request
from protocol.models import Diagnosis, Telemetry
from services.anomaly import detect
from services.state import StateStore
from services.mqtt_consumer import TelemetryConsumer
from agent.knowledge import KnowledgeBase
from agent.provider import from_environment
from agent.repository import RunRepository
from agent.runtime import DiagnosticRuntime

store = StateStore()
consumer = TelemetryConsumer(store)




@asynccontextmanager
async def lifespan(app: FastAPI):
    repository = RunRepository(os.getenv("DIAGNOSTIC_DB", "data/diagnostics.sqlite3"))
    app.state.runtime = DiagnosticRuntime(repository, KnowledgeBase(), from_environment())
    consumer.start()
    try:
        yield
    finally:
        consumer.stop()
        repository.close()


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
def telemetry(device_id: str, limit: int = Query(default=100, ge=1, le=300)):
    return {"device_id": device_id, "items": [item.model_dump(mode="json") for item in store.history(device_id, min(limit, 300))]}


@app.get("/v1/incidents/{incident_id}")
def incident(incident_id: str):
    item = store.get_incident(incident_id)
    if item is None:
        raise HTTPException(status_code=404, detail="incident not found")
    return item


@app.post("/v1/incidents/{incident_id}/diagnose", response_model=Diagnosis)
async def diagnose(incident_id: str, request: Request):
    run = await run_diagnosis(incident_id, request)
    return run["diagnosis"]


async def run_diagnosis(incident_id: str, request: Request):
    incident = store.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="incident not found")
    return await request.app.state.runtime.run(
        incident.model_copy(deep=True), store.history(incident.device_id, 300))


@app.post("/v1/incidents/{incident_id}/runs")
async def create_run(incident_id: str, request: Request):
    """Bounded synchronous workflow; return trace_id plus validated diagnosis."""
    return await run_diagnosis(incident_id, request)


@app.get("/v1/runs/{trace_id}")
def get_run(trace_id: str, request: Request):
    run = request.app.state.runtime.repository.get(trace_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run


@app.get("/v1/tools")
def tools():
    from agent.tools import build_registry
    return build_registry([], None, KnowledgeBase()).definitions()


@app.get("/v1/incidents")
def incidents():
    return {"items": store.list_incidents()}
