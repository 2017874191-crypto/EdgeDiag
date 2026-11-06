import asyncio
import json
import pytest
from agent.knowledge import KnowledgeBase
from agent.registry import Arguments, ToolContext, ToolRegistry, ToolSpec
from agent.repository import RunRepository
from agent.runtime import DiagnosticRuntime
from agent.tools import build_registry
from protocol.models import Incident, Telemetry


def sample(seq=1, boot="boot-1", timestamp=100):
    return Telemetry(device_id="motor-1", boot_id=boot, seq=seq, timestamp_ms=timestamp,
                     firmware_version="sim", metrics={"temperature_c": 85, "vibration_rms": 1.8})


def incident():
    return Incident(incident_id="inc-1", device_id="motor-1", type="thermal_vibration",
                    severity="high", confidence=0.9, evidence={"temperature_c": 85})


class FakeProvider:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.messages = []

    async def complete(self, system, messages, tools):
        self.messages.append(json.loads(json.dumps(messages)))
        item = next(self.responses)
        if isinstance(item, Exception):
            raise item
        return item


def final(evidence_ids=None, document_ids=None):
    return {"stop_reason": "end_turn", "usage": {"input_tokens": 10}, "content": [
        {"type": "text", "text": json.dumps({
            "summary": "温度振动联合异常，根因待核验。",
            "hypotheses": ["需核实负载与安装状态"], "checks": ["人员确认断电后检查安装"],
            "evidence_ids": evidence_ids or ["ev-1"], "document_ids": document_ids or [],
        })}]}


def run_with(provider=None, timeout_s=60):
    repo = RunRepository(":memory:")
    rt = DiagnosticRuntime(repo, KnowledgeBase(), provider, timeout_s)
    result = asyncio.run(rt.run(incident(), [sample()]))
    saved = repo.get(result["trace_id"])
    assert saved == result
    repo.close()
    return result


def test_rules_workflow_collects_real_tool_results():
    run = run_with()
    assert run["status"] == "completed"
    assert run["diagnosis"]["mode"] == "rules"
    assert run["diagnosis"]["status"] == "degraded"
    assert run["diagnosis"]["confidence"] == 0
    assert len(run["events"]) == 3
    assert all(e["status"] == "ok" for e in run["events"])
    assert all(e["source"] != "search_device_manual" for e in run["diagnosis"]["evidence"])


def test_model_tool_use_roundtrip():
    provider = FakeProvider([
        {"stop_reason": "tool_use", "content": [{"type": "tool_use", "id": "call-1",
         "name": "query_telemetry", "input": {"device_id": "motor-1", "limit": 1}}]},
        final(["ev-1", "ev-5"], ["demo-motor-vibration-v1"]),
    ])
    run = run_with(provider)
    assert run["diagnosis"]["mode"] == "agent"
    assert run["diagnosis"]["requires_confirmation"]
    result = provider.messages[1][-1]["content"][0]
    assert result["tool_use_id"] == "call-1"
    assert json.loads(result["content"])["data"][0]["seq"] == 1


def test_hallucinated_citations_are_rejected_and_repaired():
    run = run_with(FakeProvider([final(["invented"]), final()]))
    assert run["diagnosis"]["mode"] == "agent"
    assert any(e.get("error") == "invalid_output_or_citations" for e in run["events"])


def test_invalid_model_output_exhausts_budget_and_falls_back():
    run = run_with(FakeProvider([final(document_ids=["invented"])] * 6))
    assert run["diagnosis"]["fallback_reason"] == "step_budget_exceeded"


def test_provider_error_retries_then_falls_back_without_leaking_error():
    run = run_with(FakeProvider([ConnectionError("secret-api-key")] * 2))
    assert run["diagnosis"]["fallback_reason"] == "provider_failed"
    assert "secret-api-key" not in json.dumps(run)


def test_malformed_provider_envelope_falls_back():
    run = run_with(FakeProvider([{"content": "not-blocks"}] * 2))
    assert run["diagnosis"]["fallback_reason"] == "provider_failed"


def test_model_cannot_read_other_device():
    provider = FakeProvider([
        {"stop_reason": "tool_use", "content": [{"type": "tool_use", "id": "cross",
         "name": "query_telemetry", "input": {"device_id": "other"}}]}, final(),
    ])
    run = run_with(provider)
    assert run["diagnosis"]["mode"] == "agent"
    result = provider.messages[1][-1]["content"][0]
    assert result["is_error"] is True
    assert json.loads(result["content"])["error"] == "device_scope_denied"


def test_tool_policy_blocks_unknown_write_and_wrong_device():
    async def scenario():
        registry = build_registry([sample()], incident(), KnowledgeBase())
        async def forbidden(ctx, args):
            raise AssertionError("must not execute")
        registry.register(ToolSpec("write", "", Arguments, forbidden, read_only=False))
        ctx = ToolContext("motor-1", "inc-1")
        trace = []
        for name, args, error in [
            ("shell", {}, "unknown_tool"), ("write", {}, "write_forbidden"),
            ("get_device_status", {"device_id": "other"}, "device_scope_denied"),
            ("query_telemetry", {"device_id": "motor-1", "limit": 999}, "invalid_arguments"),
            ("query_telemetry", {"device_id": "motor-1", "limit": "2"}, "invalid_arguments"),
        ]:
            result = await registry.execute(name, args, ctx, trace)
            assert result["error"] == error
        assert len(trace) == 5
    asyncio.run(scenario())


def test_retry_and_timeout_are_bounded():
    async def scenario():
        attempts = []
        async def slow(ctx, args):
            attempts.append(1)
            await asyncio.sleep(1)
        r = ToolRegistry()
        r.register(ToolSpec("slow", "", Arguments, slow, timeout_s=0.001))
        trace = []
        result = await r.execute("slow", {}, ToolContext("motor-1", "inc-1"), trace)
        assert result["error"] == "tool_timeout"
        assert len(attempts) == 2
        assert trace[0]["attempts"] == 2
    asyncio.run(scenario())


def test_workflow_timeout_persists_degraded_result():
    class Slow:
        async def complete(self, *args):
            await asyncio.sleep(10)
    run = run_with(Slow(), timeout_s=0.02)
    assert run["diagnosis"]["fallback_reason"] == "workflow_timeout"


def test_cancellation_is_not_swallowed():
    async def scenario():
        class Slow:
            async def complete(self, *args):
                await asyncio.sleep(10)
        repo = RunRepository(":memory:")
        task = asyncio.create_task(DiagnosticRuntime(repo, KnowledgeBase(), Slow()).run(incident(), [sample()]))
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        records = repo.db.execute("SELECT body FROM runs").fetchall()
        assert json.loads(records[0][0])["status"] == "cancelled"
        repo.close()
    asyncio.run(scenario())


def test_runs_survive_restart_and_incomplete_runs_are_marked(tmp_path):
    path = tmp_path / "runs.sqlite3"
    repo = RunRepository(path)
    repo.save({"trace_id": "interrupted", "status": "running"})
    repo.save({"trace_id": "done", "status": "completed"})
    repo.close()
    repo = RunRepository(path)
    assert repo.get("interrupted")["status"] == "interrupted"
    assert repo.get("done")["status"] == "completed"
    repo.close()


def test_empty_retrieval_and_invalid_samples():
    assert KnowledgeBase().search("unknown-jargon") == []
    invalid = sample().model_copy(update={"quality": sample().quality.model_copy(update={"sensor_valid": False})})
    async def scenario():
        r = build_registry([invalid], incident(), KnowledgeBase())
        result = await r.execute("calculate_signal_features",
                                 {"device_id": "motor-1", "metric": "temperature_c"},
                                 ToolContext("motor-1", "inc-1"), [])
        assert result["data"]["count"] == 0
    asyncio.run(scenario())


def test_api_returns_trace_and_persists_it():
    from fastapi.testclient import TestClient
    from services.app import app
    from tests.test_service import trigger
    with TestClient(app) as client:
        iid = trigger(client)
        run = client.post(f"/v1/incidents/{iid}/runs").json()
        assert run["diagnosis"]["status"] == "degraded"
        assert client.get("/v1/runs/" + run["trace_id"]).json() == run
        assert len(client.get("/v1/tools").json()) == 5
        assert client.get("/v1/devices/motor-1/telemetry?limit=-1").status_code == 422
        assert client.post("/v1/incidents/missing/runs").status_code == 404
