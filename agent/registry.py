"""Tool contracts inspired by DiAn AgentToolSpec and Orbit ToolRegistry."""
import asyncio
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Any
from pydantic import BaseModel, ConfigDict, ValidationError


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


@dataclass(frozen=True)
class ToolContext:
    device_id: str
    incident_id: str


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    arguments: type[BaseModel]
    handler: Callable[[ToolContext, BaseModel], Awaitable[Any]]
    timeout_s: float = 2.0
    retry_safe: bool = True
    read_only: bool = True


class ToolRegistry:
    def __init__(self):
        self._tools = {}

    def register(self, tool: ToolSpec):
        if tool.name in self._tools or tool.timeout_s <= 0:
            raise ValueError("duplicate tool or invalid timeout")
        self._tools[tool.name] = tool

    def definitions(self):
        return [{"name": t.name, "description": t.description,
                 "input_schema": t.arguments.model_json_schema()}
                for t in self._tools.values() if t.read_only]

    async def execute(self, name, arguments, context, trace):
        start = time.perf_counter()
        tool = self._tools.get(name)
        error = None
        attempts = 0
        data = None
        try:
            if tool is None:
                raise PermissionError("unknown_tool")
            if not tool.read_only:
                raise PermissionError("write_forbidden")
            args = tool.arguments.model_validate(arguments)
            if hasattr(args, "device_id") and args.device_id != context.device_id:
                raise PermissionError("device_scope_denied")
            for attempts in range(1, 3 if tool.retry_safe else 2):
                try:
                    data = await asyncio.wait_for(tool.handler(context, args), tool.timeout_s)
                    break
                except (TimeoutError, ConnectionError):
                    if not tool.retry_safe or attempts == 2:
                        raise
        except asyncio.CancelledError:
            trace.append({"kind": "tool", "name": name, "status": "cancelled"})
            raise
        except PermissionError as exc:
            error = str(exc)
        except ValidationError:
            error = "invalid_arguments"
        except TimeoutError:
            error = "tool_timeout"
        except Exception:
            error = "tool_failed"  # Do not persist exceptions containing credentials.
        result = {"ok": error is None, "data": data, "error": error}
        trace.append({"kind": "tool", "name": name, "status": "ok" if error is None else "error",
                      "arguments": arguments, "attempts": attempts,
                      "duration_ms": round((time.perf_counter() - start) * 1000, 3),
                      "result": result})
        return result
