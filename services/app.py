from contextlib import asynccontextmanager
import os
from pathlib import Path
from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from protocol.models import Diagnosis, Telemetry
from services.state import StateStore, ConflictError, CapacityError
from services.mqtt_consumer import TelemetryConsumer
from services.jobs import DiagnosticJobs
from agent.knowledge import KnowledgeBase
from agent.provider import from_environment
from agent.repository import RunRepository
from agent.runtime import DiagnosticRuntime


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_key: str | None = Field(default=None, min_length=1, max_length=128)
    expected_revision: int | None = Field(default=None, ge=1)


def create_app():
    @asynccontextmanager
    async def lifespan(app):
        provider = from_environment()
        run_path = os.getenv("DIAGNOSTIC_DB", "data/diagnostics.sqlite3")
        domain_path = os.getenv("DOMAIN_DB", str(Path(run_path).with_name("domain.sqlite3")))
        store = StateStore(domain_path)
        repository = RunRepository(run_path)
        runtime = DiagnosticRuntime(repository, KnowledgeBase(), provider)
        consumer = TelemetryConsumer(store)
        jobs = DiagnosticJobs(store, runtime)
        app.state.store, app.state.runtime, app.state.jobs = store, runtime, jobs
        jobs.start()
        consumer.start()
        try:
            yield
        finally:
            consumer.stop()
            await jobs.close()
            repository.close()
            store.close()

    app = FastAPI(title="EdgeDiag", version="0.2.0", lifespan=lifespan)

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "edgediag", "version": "0.2.0"}

    @app.post("/v1/telemetry", status_code=202)
    def ingest(message: Telemetry, request: Request):
        try:
            result = request.app.state.store.ingest(message)
        except ConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {**result, "device_id": message.device_id, "seq": message.seq}

    @app.get("/v1/devices/{device_id}/telemetry")
    def telemetry(device_id: str, request: Request, limit: int = Query(default=100, ge=1, le=300)):
        return {"device_id": device_id, "items": request.app.state.store.history(device_id, limit)}

    @app.get("/v1/incidents")
    def incidents(request: Request, limit: int = Query(default=100, ge=1, le=300)):
        return {"items": request.app.state.store.list_incidents(limit)}

    @app.get("/v1/incidents/{incident_id}")
    def incident(incident_id: str, request: Request):
        item = request.app.state.store.get_incident(incident_id)
        if item is None:
            raise HTTPException(404, "incident not found")
        return item

    @app.get("/v1/incidents/{incident_id}/events")
    def events(incident_id: str, request: Request):
        if request.app.state.store.get_incident(incident_id) is None:
            raise HTTPException(404, "incident not found")
        return {"items": request.app.state.store.incident_events(incident_id)}

    def submit(incident_id, request, body):
        body = body or RunRequest()
        try:
            job, created = request.app.state.store.submit_job(
                incident_id, body.request_key, body.expected_revision)
        except KeyError as exc:
            raise HTTPException(404, "incident not found") from exc
        except ConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
        except CapacityError as exc:
            raise HTTPException(429, str(exc), headers={"Retry-After": "1"}) from exc
        return job

    @app.post("/v1/incidents/{incident_id}/jobs", status_code=202)
    async def create_job(incident_id: str, request: Request, body: RunRequest | None = None):
        return submit(incident_id, request, body)

    @app.get("/v1/jobs/{job_id}")
    def get_job(job_id: str, request: Request):
        job = request.app.state.store.get_job(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        return job

    async def run_diagnosis(incident_id, request, body):
        job = submit(incident_id, request, body)
        try:
            job = await request.app.state.jobs.wait(job["job_id"])
        except TimeoutError as exc:
            raise HTTPException(504, {"job_id": job["job_id"], "message": "poll job or retry same request"}) from exc
        if job["status"] != "completed":
            raise HTTPException(503, {"job_id": job["job_id"], "error": job["error"]})
        return job["result"]

    @app.post("/v1/incidents/{incident_id}/runs")
    async def create_run(incident_id: str, request: Request, body: RunRequest | None = None):
        return await run_diagnosis(incident_id, request, body)

    @app.post("/v1/incidents/{incident_id}/diagnose", response_model=Diagnosis)
    async def diagnose(incident_id: str, request: Request, body: RunRequest | None = None):
        return (await run_diagnosis(incident_id, request, body))["diagnosis"]

    @app.get("/v1/runs/{trace_id}")
    def get_run(trace_id: str, request: Request):
        run = request.app.state.runtime.repository.get(trace_id)
        if run is None:
            raise HTTPException(404, "run not found")
        return run

    @app.get("/v1/tools")
    def tools():
        from agent.tools import build_registry
        return build_registry([], None, KnowledgeBase()).definitions()

    return app


app = create_app()
