"""Execution tracing (ADR-009).

Modelled on OpenTelemetry span semantics but stored in our own Postgres, because the trace is a *product*
surface here ("what did this cost, why that model, what was checked"), not just ops telemetry.

Privacy: raw prompts are redacted unless `log_raw_prompts` is explicitly enabled (rule 28). The trace records
shapes, decisions and costs by default — enough to debug and to explain, without retaining user content.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SpanKind(str, Enum):
    ORCHESTRATION = "orchestration"
    SIGNALS = "signals"
    INTENT = "intent"
    OPTIMIZE = "optimize"
    ROUTE = "route"
    GENERATE = "generate"
    EVALUATE = "evaluate"
    JUDGE = "judge"
    SYNTHESIZE = "synthesize"
    CACHE = "cache"


class SpanStatus(str, Enum):
    OK = "ok"
    ERROR = "error"


@dataclass
class Span:
    name: str
    kind: SpanKind
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    parent_id: str | None = None
    started_at: float = field(default_factory=time.time)
    duration_ms: int = 0
    status: SpanStatus = SpanStatus.OK
    error: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    cost_usd: float = 0.0

    def set(self, **attrs: Any) -> None:
        self.attributes.update(attrs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "name": self.name,
            "kind": self.kind.value,
            "duration_ms": self.duration_ms,
            "status": self.status.value,
            "error": self.error,
            "attributes": self.attributes,
            "cost_usd": round(self.cost_usd, 6),
        }


@dataclass
class Trace:
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    spans: list[Span] = field(default_factory=list)
    _stack: list[str] = field(default_factory=list)

    @contextmanager
    def span(self, name: str, kind: SpanKind, **attrs: Any) -> Iterator[Span]:
        s = Span(
            name=name,
            kind=kind,
            parent_id=self._stack[-1] if self._stack else None,
            attributes=dict(attrs),
        )
        self.spans.append(s)
        self._stack.append(s.span_id)
        started = time.perf_counter()
        try:
            yield s
        except Exception as exc:
            s.status = SpanStatus.ERROR
            s.error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            s.duration_ms = int((time.perf_counter() - started) * 1000)
            self._stack.pop()

    @property
    def total_cost_usd(self) -> float:
        return round(sum(s.cost_usd for s in self.spans), 6)

    @property
    def total_duration_ms(self) -> int:
        roots = [s for s in self.spans if s.parent_id is None]
        return max((s.duration_ms for s in roots), default=0)

    def llm_call_count(self) -> int:
        """Number of provider calls made.

        Directly exposed in the product: "this answer cost N model calls" is the honest way to show that
        the orchestrator is spending proportionally.
        """
        return len([s for s in self.spans if s.kind in (SpanKind.GENERATE, SpanKind.INTENT, SpanKind.JUDGE)])

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "total_cost_usd": self.total_cost_usd,
            "total_duration_ms": self.total_duration_ms,
            "llm_calls": self.llm_call_count(),
            "spans": [s.to_dict() for s in self.spans],
        }


class TraceExporter:
    """Seam for exporting to Langfuse/OTLP later (ADR-009). No-op by default."""

    async def export(self, trace: Trace) -> None:
        return None
