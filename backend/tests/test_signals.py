"""Tests for the heuristic signal extractor (ADR-003).

These matter more than they look: the extractor decides whether we spend money. A regression here either
burns cost on trivial requests or mis-handles hard ones.
"""

from __future__ import annotations

import pytest

from app.pipeline.signals import extract_signals
from app.pipeline.types import Ambiguity, Complexity, RiskLevel, TaskType


class TestTaskClassification:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ('Translate "good morning" to Spanish', TaskType.TRANSLATION),
            ("make me a resume for a software internship", TaskType.CAREER),
            ("Extract the name and email from this text as JSON", TaskType.STRUCTURED_EXTRACTION),
            ("Summarize this article in 100 words", TaskType.SUMMARIZATION),
            ("My app crashes with a TypeError, here is the traceback", TaskType.DEBUGGING),
            ("Design a scalable system architecture for video streaming", TaskType.SOFTWARE_ARCHITECTURE),
            ("Write a blog post about remote work", TaskType.WRITING),
            ("Classify these reviews by sentiment", TaskType.CLASSIFICATION),
        ],
    )
    def test_classifies_common_requests(self, text: str, expected: TaskType) -> None:
        assert extract_signals(text).likely_task is expected


class TestRiskDetection:
    @pytest.mark.parametrize(
        "text",
        [
            "What dosage of ibuprofen should I take for a headache?",
            "Should I sue my landlord for this?",
            "How should I invest my retirement savings?",
        ],
    )
    def test_flags_high_risk_domains(self, text: str) -> None:
        assert extract_signals(text).risk is RiskLevel.HIGH

    def test_high_risk_suppresses_fast_path_confidence(self) -> None:
        """A high-risk request must never look confidently simple, whatever its surface form."""
        sig = extract_signals("What dosage of ibuprofen should I take?")
        assert sig.risk is RiskLevel.HIGH
        assert sig.confidence < 0.5


class TestComplexity:
    def test_trivial_question_is_trivial(self) -> None:
        assert extract_signals("What is the capital of France?").complexity is Complexity.TRIVIAL

    def test_open_ended_build_is_not_low_complexity(self) -> None:
        """Brevity is missing specification, not absent work.

        'build a landing page for my startup' is six words and a week of decisions.
        """
        sig = extract_signals("build a landing page for my startup")
        assert sig.complexity.rank >= Complexity.MEDIUM.rank
        assert "open_ended_build" in sig.matched_rules

    def test_multi_part_request_raises_complexity(self) -> None:
        sig = extract_signals(
            "Design a microservice architecture for 10M users and also plan the migration "
            "from our existing monolith"
        )
        assert sig.complexity.rank >= Complexity.HIGH.rank


class TestAmbiguity:
    def test_vague_request_is_ambiguous(self) -> None:
        assert extract_signals("help me with something").ambiguity.rank >= Ambiguity.MEDIUM.rank

    def test_explicit_specification_reduces_ambiguity(self) -> None:
        vague = extract_signals("write a blog post")
        specific = extract_signals(
            "write a blog post for a technical audience in under 500 words as markdown"
        )
        assert specific.ambiguity.rank < vague.ambiguity.rank


class TestFlags:
    def test_detects_freshness_requirement(self) -> None:
        sig = extract_signals("what is today's price of bitcoin?")
        assert sig.needs_current_info
        assert sig.needs_tools

    def test_detects_multimodal_requirement(self) -> None:
        assert extract_signals("analyze this PDF").needs_multimodal

    def test_detects_explicit_format(self) -> None:
        assert extract_signals("give me the result as JSON").has_explicit_format

    def test_detects_constraints(self) -> None:
        assert extract_signals("summarize this in under 100 words").has_constraints


class TestConfidence:
    def test_well_specified_simple_request_is_high_confidence(self) -> None:
        assert extract_signals('Translate "good morning" to Spanish').confidence >= 0.8

    def test_vague_request_is_low_confidence(self) -> None:
        assert extract_signals("help me with something").confidence < 0.4

    def test_very_short_input_is_low_confidence(self) -> None:
        assert extract_signals("do it").confidence < 0.4


class TestPurity:
    def test_extraction_is_deterministic(self) -> None:
        """Required for reproducible orchestration decisions and stable tests."""
        text = "Design a scalable architecture and plan the migration"
        first = extract_signals(text)
        second = extract_signals(text)
        assert first.complexity is second.complexity
        assert first.confidence == second.confidence
        assert first.matched_rules == second.matched_rules

    def test_handles_empty_and_whitespace_input(self) -> None:
        for text in ("", "   ", "\n\n"):
            sig = extract_signals(text)
            assert sig.word_count == 0
            assert sig.confidence < 0.5
