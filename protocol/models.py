from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field


class Quality(BaseModel):
    sensor_valid: bool = True
    dropped_samples: int = Field(default=0, ge=0)


class Telemetry(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    schema_version: Literal[1] = 1
    device_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    boot_id: str = Field(min_length=1, max_length=128)
    seq: int = Field(ge=0)
    timestamp_ms: int = Field(ge=0)
    firmware_version: str = Field(min_length=1, max_length=64)
    metrics: dict[str, float] = Field(min_length=1)
    quality: Quality = Field(default_factory=Quality)


class Incident(BaseModel):
    incident_id: str
    device_id: str
    type: Literal["thermal", "vibration", "thermal_vibration", "sensor_invalid", "offline"]
    severity: Literal["low", "medium", "high"]
    status: Literal["open", "diagnosing", "diagnosed", "resolved"] = "open"
    evidence: dict[str, Any] = Field(default_factory=dict)
    detector: str = "rule_v1"
    confidence: float = Field(ge=0, le=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    revision: int = Field(default=1, ge=1)
    occurrences: int = Field(default=1, ge=1)


class Diagnosis(BaseModel):
    incident_id: str
    device_id: str
    status: Literal["diagnosed", "degraded", "failed"]
    severity: Literal["low", "medium", "high"]
    summary: str
    confidence: float = Field(ge=0, le=1)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    hypotheses: list[dict[str, Any]] = Field(default_factory=list)
    recommendations: list[dict[str, Any]] = Field(default_factory=list)
    requires_confirmation: bool = False
    mode: Literal["rules", "agent"] = "rules"
    fallback_reason: str | None = None
    document_ids: list[str] = Field(default_factory=list)


class Command(BaseModel):
    command_id: str
    device_id: str
    type: Literal["set_sampling_interval"]
    args: dict[str, Any]
    issued_by: str = "diagnostic-service"
    expires_at: int | None = None
