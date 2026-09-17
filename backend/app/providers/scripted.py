"""Deterministic offline provider.

Purpose: make the *entire* pipeline exercisable with no API keys and no network, so CI is meaningful and the
benchmark harness can validate its own mechanics before real money is spent.

This is explicitly a test/development double (rule 60). It is isolated here, it is never registered when
`environment == "production"` unless `THROUGHLINE_ALLOW_SCRIPTED_PROVIDER=1` is set, and it never pretends to
be a real model: responses are echoes of the input, not fabricated model-quality answers.

**It must never be used to produce a benchmark quality number.** It exists to prove plumbing, not quality.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.core.errors import ProviderInvalidRequest
from app.pipeline.types import TokenUsage
from app.providers.base import Capability, ModelRequest, ModelResponse, Provider

# Rough token estimate. Deliberately crude and named as such — real usage always comes from the provider.
CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


class ScriptedProvider(Provider):
    """Returns deterministic, input-derived output.

    Deterministic by construction: identical requests produce identical responses, which is what lets tests
    assert on orchestration behaviour (did we make 1 call or 3?) without flakiness.
    """

    name = "scripted"

    # Behaviours a test can request via the model id suffix, e.g. "scripted:fail".
    def __init__(self, api_key: str = "offline", base_url: str = "") -> None:
        super().__init__(api_key=api_key or "offline", base_url=base_url)
        self.calls: list[ModelRequest] = []

    @property
    def configured(self) -> bool:
        return True

    def supports(self, cap: Capability) -> bool:
        return cap in {
            Capability.TEXT,
            Capability.STRUCTURED_OUTPUT,
            Capability.STREAMING,
            Capability.PROMPT_CACHING,
        }

    async def complete(self, req: ModelRequest) -> ModelResponse:
        self.calls.append(req)
        user_text = "\n".join(m.content for m in req.messages if m.role == "user")
        behaviour = req.model_id.split(":")[-1] if ":" in req.model_id else "ok"

        if behaviour == "invalid":
            raise ProviderInvalidRequest(
                "scripted provider asked to fail", provider=self.name, model_id=req.model_id
            )

        if req.output_schema is not None:
            text = self._synthesize_json(req.output_schema, user_text, broken=behaviour == "badjson")
        else:
            text = self._synthesize_text(user_text, req.system, behaviour)

        usage = TokenUsage(
            input_tokens=estimate_tokens(req.system + user_text),
            output_tokens=estimate_tokens(text),
        )
        return ModelResponse(
            text=text,
            usage=usage,
            model_id=req.model_id,
            provider=self.name,
            latency_ms=1,
            raw_id=hashlib.sha256((req.model_id + user_text).encode()).hexdigest()[:16],
        )

    # -- synthesis helpers ------------------------------------------------------------
    def _synthesize_text(self, user_text: str, system: str, behaviour: str) -> str:
        """Echo the request back in a structured way.

        Crucially this is *derived from the input*, so a test asserting "the compiled prompt contained the
        output-format instruction" can verify it round-tripped, and nothing here resembles a quality answer.
        """
        if behaviour == "empty":
            return ""
        if behaviour == "short":
            return "ok"

        digest = hashlib.sha256((system + user_text).encode()).hexdigest()[:8]
        lines = [
            f"[scripted:{digest}] Deterministic offline response.",
            "",
            "Request echo:",
            user_text.strip()[:600] or "(empty)",
        ]
        if behaviour == "long":
            lines.append("filler " * 400)
        return "\n".join(lines)

    def _synthesize_json(self, schema: dict[str, Any], user_text: str, *, broken: bool) -> str:
        obj = _example_from_schema(schema, user_text)
        payload = json.dumps(obj, indent=2)
        if broken:
            payload = payload[: max(1, len(payload) // 2)]  # truncated -> invalid JSON, on purpose
        return payload


def _example_from_schema(schema: dict[str, Any], seed_text: str) -> Any:
    """Build a schema-conforming example.

    Used only by the scripted provider. It follows the schema rather than inventing plausible content, which
    keeps the distinction between "valid shape" and "good answer" honest.
    """
    t = schema.get("type")
    if "enum" in schema and schema["enum"]:
        return schema["enum"][0]
    if t == "object":
        props: dict[str, Any] = schema.get("properties", {})
        required = schema.get("required", list(props.keys()))
        return {k: _example_from_schema(v, seed_text) for k, v in props.items() if k in required}
    if t == "array":
        item = schema.get("items", {"type": "string"})
        return [_example_from_schema(item, seed_text)]
    if t == "integer":
        return 1
    if t == "number":
        return 1.0
    if t == "boolean":
        return True
    if t == "null":
        return None
    # Deliberately a neutral marker rather than words lifted from the prompt. Echoing prompt text into
    # string fields made offline traces look like real (and confusingly wrong) model output, which
    # obscures whether the pipeline or the double is misbehaving.
    return "scripted-value"
