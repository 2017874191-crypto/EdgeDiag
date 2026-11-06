import math
import statistics
from typing import Literal
from pydantic import Field
from agent.registry import Arguments, ToolRegistry, ToolSpec


class DeviceArgs(Arguments):
    device_id: str = Field(min_length=1, max_length=128)


class HistoryArgs(DeviceArgs):
    limit: int = Field(default=100, ge=1, le=300)


class FeatureArgs(HistoryArgs):
    metric: Literal["temperature_c", "vibration_rms", "current_a"]


class SearchArgs(Arguments):
    query: str = Field(min_length=1, max_length=300)
    limit: int = Field(default=3, ge=1, le=5)


def build_registry(history, incident, knowledge):
    """Tools read an immutable per-run snapshot, never live shared state."""
    registry = ToolRegistry()

    async def status(ctx, args):
        latest = max(history, key=lambda m: m.timestamp_ms) if history else None
        return {"device_id": ctx.device_id, "sample_count": len(history),
                "latest": latest.model_dump(mode="json") if latest else None}

    async def telemetry(ctx, args):
        return [m.model_dump(mode="json") for m in history[-args.limit:]]

    async def features(ctx, args):
        values = [m.metrics[args.metric] for m in history[-args.limit:]
                  if m.quality.sensor_valid and args.metric in m.metrics]
        if not values:
            return {"metric": args.metric, "count": 0}
        return {"metric": args.metric, "count": len(values),
                "mean": statistics.mean(values), "min": min(values), "max": max(values),
                "stddev": statistics.pstdev(values),
                "rms": math.sqrt(statistics.mean(x*x for x in values))}

    async def context(ctx, args):
        return incident.model_dump(mode="json")

    async def search(ctx, args):
        return knowledge.search(args.query, args.limit)

    for name, description, arguments, handler in [
        ("get_device_status", "Read the incident device snapshot; does not prove current online status.", DeviceArgs, status),
        ("query_telemetry", "Read up to 300 telemetry samples frozen at diagnosis start.", HistoryArgs, telemetry),
        ("calculate_signal_features", "Compute statistics on valid samples; RMS here is over telemetry features, not raw vibration waveform.", FeatureArgs, features),
        ("get_incident_context", "Read detector evidence and severity for this incident.", Arguments, context),
        ("search_device_manual", "Search versioned synthetic demo guidance by English fault type keywords. Empty means no supporting manual.", SearchArgs, search),
    ]:
        registry.register(ToolSpec(name, description, arguments, handler))
    return registry
