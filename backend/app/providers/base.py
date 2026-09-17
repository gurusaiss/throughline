"""Provider protocol and shared request/response shapes.

Adapters own four things the core must never see (ARCHITECTURE §5):
  1. authentication
  2. request/response translation
  3. JSON-Schema dialect translation (ADR-010 — providers honour different subsets)
  4. error normalisation into `app.core.errors`

Nothing in `app.pipeline` or `app.evaluation` may import a concrete adapter.
"""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.pipeline.types import TokenUsage


class Capability(str, Enum):
    TEXT = "text"
    VISION = "vision"
    STRUCTURED_OUTPUT = "structured_output"
    TOOL_USE = "tool_use"
    STREAMING = "streaming"
    REASONING = "reasoning"
    PROMPT_CACHING = "prompt_caching"
    WEB_SEARCH = "web_search"
    LONG_CONTEXT = "long_context"


class Message(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class ModelRequest(BaseModel):
    model_id: str
    system: str = ""
    messages: list[Message] = Field(default_factory=list)
    max_output_tokens: int = 4096
    temperature: float | None = None
    output_schema: dict[str, Any] | None = None
    stop_sequences: list[str] = Field(default_factory=list)
    timeout_seconds: float = 90.0
    enable_prompt_cache: bool = True
    cacheable_prefix_chars: int = 0
    metadata: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def from_prompt(cls, model_id: str, system: str, user: str, **kw: Any) -> ModelRequest:
        return cls(model_id=model_id, system=system, messages=[Message(role="user", content=user)], **kw)


class ModelResponse(BaseModel):
    text: str = ""
    parsed: dict[str, Any] | None = None
    usage: TokenUsage = Field(default_factory=TokenUsage)
    finish_reason: str = "stop"
    model_id: str = ""
    provider: str = ""
    latency_ms: int = 0
    raw_id: str = ""


class Chunk(BaseModel):
    delta: str = ""
    done: bool = False


class Provider(abc.ABC):
    """Base class for all model providers."""

    name: str = "base"

    def __init__(self, api_key: str = "", base_url: str = "") -> None:
        self._api_key = api_key
        self._base_url = base_url

    @property
    def configured(self) -> bool:
        """Whether this adapter has a usable credential.

        The router filters on this so an unconfigured provider is never selected and then fails at call
        time — the failure belongs at routing, where a fallback is still possible.
        """
        return bool(self._api_key)

    @abc.abstractmethod
    async def complete(self, req: ModelRequest) -> ModelResponse: ...

    async def stream(self, req: ModelRequest) -> AsyncIterator[Chunk]:
        """Default: adapters that cannot stream degrade to one terminal chunk."""
        resp = await self.complete(req)
        yield Chunk(delta=resp.text, done=True)

    async def aclose(self) -> None:
        return None
