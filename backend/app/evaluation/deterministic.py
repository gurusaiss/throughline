"""Deterministic evaluators.

These are the *authoritative* checks: machine-verifiable facts about an output, with no model opinion
involved. Where a deterministic check and a judge disagree, the deterministic check wins.

They are also free and instant, which is why FAST and BALANCED modes run them and skip the judge entirely.

ADR-010 note: we validate structured output locally even when the provider claims constrained decoding.
Reported compliance is 98–100% (RESEARCH 3.4); at SaaS volume the gap between 99% and 100% is a support
queue, and it is this validator that makes it safe to depend on the provider's guarantee.
"""

from __future__ import annotations

import json
import re
from typing import Any

import jsonschema

from app.evaluation.types import CheckStatus, Criterion, DeterministicCheck
from app.pipeline.types import Candidate, ExecutionSpec, OutputFormat

MAX_SCHEMA_ERRORS_REPORTED = 5


def run_all(candidate: Candidate, spec: ExecutionSpec) -> list[DeterministicCheck]:
    """Run every applicable deterministic check for this spec."""
    checks: list[DeterministicCheck] = [check_non_empty(candidate)]
    if not candidate.ok:
        return checks

    if spec.output_schema is not None:
        checks.append(check_json_valid(candidate))
        checks.append(check_schema_compliance(candidate, spec.output_schema))
    elif spec.output_format is OutputFormat.JSON:
        checks.append(check_json_valid(candidate))

    checks.extend(check_constraints(candidate, spec))
    optional_checks = [check_format_shape(candidate, spec), check_no_placeholder_content(candidate, spec)]
    checks.extend(c for c in optional_checks if c is not None)
    return checks


# --------------------------------------------------------------------------------------
# Individual checks
# --------------------------------------------------------------------------------------
def check_non_empty(candidate: Candidate) -> DeterministicCheck:
    if candidate.error:
        return DeterministicCheck(
            name="generation_succeeded", status=CheckStatus.FAIL,
            detail=candidate.error, criterion=Criterion.CORRECTNESS,
        )
    if not candidate.text.strip():
        return DeterministicCheck(
            name="non_empty", status=CheckStatus.FAIL,
            detail="Model returned an empty response.", criterion=Criterion.COMPLETENESS,
        )
    return DeterministicCheck(name="non_empty", status=CheckStatus.PASS, criterion=Criterion.COMPLETENESS)


def check_json_valid(candidate: Candidate) -> DeterministicCheck:
    payload = _extract_json(candidate.text)
    if payload is None:
        return DeterministicCheck(
            name="json_valid", status=CheckStatus.FAIL,
            detail="Response is not parseable as JSON.", criterion=Criterion.FORMAT_COMPLIANCE,
        )
    return DeterministicCheck(
        name="json_valid", status=CheckStatus.PASS, criterion=Criterion.FORMAT_COMPLIANCE
    )


def check_schema_compliance(candidate: Candidate, schema: dict[str, Any]) -> DeterministicCheck:
    payload = _extract_json(candidate.text)
    if payload is None:
        return DeterministicCheck(
            name="schema_compliance", status=CheckStatus.SKIPPED,
            detail="Skipped: output was not valid JSON.", criterion=Criterion.FORMAT_COMPLIANCE,
        )
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(payload), key=lambda e: list(e.path))
    if not errors:
        return DeterministicCheck(
            name="schema_compliance", status=CheckStatus.PASS, criterion=Criterion.FORMAT_COMPLIANCE
        )
    detail = "; ".join(
        f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}"
        for e in errors[:MAX_SCHEMA_ERRORS_REPORTED]
    )
    if len(errors) > MAX_SCHEMA_ERRORS_REPORTED:
        detail += f" (+{len(errors) - MAX_SCHEMA_ERRORS_REPORTED} more)"
    return DeterministicCheck(
        name="schema_compliance", status=CheckStatus.FAIL,
        detail=detail, criterion=Criterion.FORMAT_COMPLIANCE,
    )


def check_constraints(candidate: Candidate, spec: ExecutionSpec) -> list[DeterministicCheck]:
    """Verify constraints that can be checked mechanically.

    Only length and inclusion/exclusion constraints are machine-checkable. A constraint like "make it
    persuasive" is left to the judge, and is reported as SKIPPED rather than silently passed — a check that
    always passes is worse than no check, because it looks like evidence.
    """
    checks: list[DeterministicCheck] = []
    text = candidate.text

    for constraint in spec.constraints:
        value = constraint.value
        limit = _parse_length_limit(value)
        if limit is not None:
            bound, unit, direction = limit
            actual = _measure(text, unit)
            ok = actual <= bound if direction == "max" else actual >= bound
            checks.append(
                DeterministicCheck(
                    name=f"length_{direction}_{bound}_{unit}",
                    status=CheckStatus.PASS if ok else CheckStatus.FAIL,
                    detail=f"{actual} {unit} vs {direction} {bound}",
                    criterion=Criterion.INSTRUCTION_FOLLOWING,
                )
            )
            continue

        must_include = _parse_must_include(value)
        if must_include:
            missing = [t for t in must_include if t.lower() not in text.lower()]
            checks.append(
                DeterministicCheck(
                    name="must_include",
                    status=CheckStatus.PASS if not missing else CheckStatus.FAIL,
                    detail=f"missing: {', '.join(missing)}" if missing else "all present",
                    criterion=Criterion.INSTRUCTION_FOLLOWING,
                )
            )
            continue

        checks.append(
            DeterministicCheck(
                name="constraint_not_machine_checkable",
                status=CheckStatus.SKIPPED,
                detail=f"'{value[:80]}' requires judgement, not a deterministic check.",
                criterion=Criterion.INSTRUCTION_FOLLOWING,
            )
        )
    return checks


def check_format_shape(candidate: Candidate, spec: ExecutionSpec) -> DeterministicCheck | None:
    """Cheap structural checks for the requested output shape."""
    text = candidate.text.strip()
    fmt = spec.output_format

    if fmt is OutputFormat.CODE:
        has_fence = "```" in text
        return DeterministicCheck(
            name="contains_code_block",
            status=CheckStatus.PASS if has_fence else CheckStatus.FAIL,
            detail="" if has_fence else "Expected a fenced code block.",
            criterion=Criterion.FORMAT_COMPLIANCE,
        )
    if fmt is OutputFormat.TABLE:
        has_table = bool(re.search(r"^\s*\|.+\|\s*$", text, re.M))
        return DeterministicCheck(
            name="contains_table",
            status=CheckStatus.PASS if has_table else CheckStatus.FAIL,
            detail="" if has_table else "Expected a Markdown table.",
            criterion=Criterion.FORMAT_COMPLIANCE,
        )
    if fmt is OutputFormat.LIST:
        has_list = bool(re.search(r"^\s*([-*+]|\d+[.)])\s+", text, re.M))
        return DeterministicCheck(
            name="contains_list",
            status=CheckStatus.PASS if has_list else CheckStatus.FAIL,
            detail="" if has_list else "Expected a list.",
            criterion=Criterion.FORMAT_COMPLIANCE,
        )
    if fmt is OutputFormat.EMAIL:
        has_subject = bool(re.search(r"^\s*subject\s*:", text, re.I | re.M))
        return DeterministicCheck(
            name="email_has_subject",
            status=CheckStatus.PASS if has_subject else CheckStatus.FAIL,
            detail="" if has_subject else "Expected a subject line.",
            criterion=Criterion.FORMAT_COMPLIANCE,
        )
    if fmt is OutputFormat.PROSE:
        # Prose was explicitly requested, so headings/bullets are a violation of the instruction.
        has_structure = bool(re.search(r"^\s*(#{1,6}\s|[-*+]\s)", text, re.M))
        return DeterministicCheck(
            name="is_prose",
            status=CheckStatus.PASS if not has_structure else CheckStatus.FAIL,
            detail="" if not has_structure else "Contains headings or bullets; prose was requested.",
            criterion=Criterion.FORMAT_COMPLIANCE,
        )
    return None


PLACEHOLDER_PATTERNS = [
    r"#\s*\.\.\.\s*(rest of|remaining|other)",
    r"//\s*\.\.\.\s*(rest of|remaining|other)",
    r"\[(your|insert|add|todo|placeholder)[^\]]*\]",
    r"\bTODO:\s*implement\b",
    r"<(your[_ ]?\w+|insert[_ ]?\w+)>",
    r"\.\.\.\s*\(rest of (the )?(code|implementation)\)",
]


def check_no_placeholder_content(candidate: Candidate, spec: ExecutionSpec) -> DeterministicCheck | None:
    """Catch elided output masquerading as a complete answer.

    Only applied where completeness was actually promised — a template for the user to fill in is a
    legitimate answer to some writing requests, but not to "write this code".
    """
    if spec.output_format not in (OutputFormat.CODE, OutputFormat.DOCUMENT):
        return None
    hits = [p for p in PLACEHOLDER_PATTERNS if re.search(p, candidate.text, re.I)]
    if hits:
        return DeterministicCheck(
            name="no_placeholder_content", status=CheckStatus.FAIL,
            detail="Output contains placeholders instead of complete content.",
            criterion=Criterion.COMPLETENESS,
        )
    return DeterministicCheck(
        name="no_placeholder_content", status=CheckStatus.PASS, criterion=Criterion.COMPLETENESS
    )


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------
_JSON_FENCE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.S)


def _extract_json(text: str) -> Any | None:
    """Parse JSON, tolerating a fenced code block or surrounding prose.

    Tolerance here is deliberate and bounded: the *format* check reports whether the model complied, while
    this extraction lets the schema check still run on a nearly-right answer so we learn what was wrong.
    """
    stripped = text.strip()
    for candidate_text in (stripped, *(m.group(1) for m in _JSON_FENCE.finditer(text))):
        try:
            return json.loads(candidate_text)
        except (json.JSONDecodeError, TypeError):
            continue
    # Last resort: the outermost {...} or [...] span.
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = stripped.find(opener), stripped.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(stripped[start : end + 1])
            except json.JSONDecodeError:
                continue
    return None


_LENGTH_RE = re.compile(
    r"(?P<dir>under|less than|no more than|at most|within|maximum of|max|at least|minimum of|exactly)"
    r"\s*(?P<n>\d+)\s*"
    r"(?P<unit>words?|characters?|chars?|sentences?|paragraphs?|lines?|bullets?|items?)",
    re.I,
)


def _parse_length_limit(value: str) -> tuple[int, str, str] | None:
    m = _LENGTH_RE.search(value)
    if not m:
        return None
    direction = "min" if m.group("dir").lower() in ("at least", "minimum of") else "max"
    unit = m.group("unit").lower().rstrip("s")
    unit = {"char": "character", "item": "bullet"}.get(unit, unit)
    return int(m.group("n")), unit, direction


def _measure(text: str, unit: str) -> int:
    if unit == "word":
        return len(text.split())
    if unit == "character":
        return len(text)
    if unit == "sentence":
        return len([s for s in re.split(r"[.!?]+(?:\s|$)", text) if s.strip()])
    if unit == "paragraph":
        return len([p for p in re.split(r"\n\s*\n", text) if p.strip()])
    if unit == "line":
        return len([ln for ln in text.splitlines() if ln.strip()])
    if unit == "bullet":
        return len(re.findall(r"^\s*([-*+]|\d+[.)])\s+", text, re.M))
    return len(text.split())


_MUST_INCLUDE_RE = re.compile(r"must (?:include|contain|have|mention)\s*:?\s*(.+)", re.I)


def _parse_must_include(value: str) -> list[str]:
    m = _MUST_INCLUDE_RE.search(value)
    if not m:
        return []
    raw = m.group(1)
    parts = re.findall(r"[\"']([^\"']+)[\"']", raw) or [
        p.strip() for p in re.split(r",| and ", raw) if p.strip()
    ]
    return [p for p in parts if 0 < len(p) < 60][:10]
