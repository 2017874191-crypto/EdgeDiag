"""Optional Anthropic Messages adapter. No external call without explicit configuration."""
import os
from typing import Protocol
import httpx


class Provider(Protocol):
    async def complete(self, system: str, messages: list, tools: list) -> dict: ...


class MessagesProvider:
    def __init__(self, base_url, api_key, model):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    async def complete(self, system, messages, tools):
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                self.base_url + "/v1/messages",
                headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
                json={"model": self.model, "max_tokens": 2048, "system": system,
                      "messages": messages, "tools": tools},
            )
            response.raise_for_status()
            return response.json()


def from_environment():
    mode = os.getenv("AGENT_PROVIDER", "rules")
    if mode == "rules":
        return None
    if mode != "anthropic":
        raise ValueError("AGENT_PROVIDER must be rules or anthropic")
    key, model = os.getenv("ANTHROPIC_API_KEY"), os.getenv("ANTHROPIC_MODEL")
    if not key or not model:
        raise ValueError("anthropic provider requires API key and model")
    return MessagesProvider(os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com"), key, model)
