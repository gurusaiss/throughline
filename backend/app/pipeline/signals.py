"""Zero-cost heuristic signal extraction (ADR-003).

This module runs before any LLM call and decides whether one is needed at all. It is the mechanism that makes
the product principle ("longer prompt != better prompt", "avoid unnecessary LLM calls") operational rather
than aspirational.

Two properties are essential and are enforced by tests:

1. **No I/O.** Pure function of a string. Free, instant, deterministic.
2. **Conservative.** `confidence` gates the FAST path. When the extractor is unsure it reports low confidence
   and the orchestrator pays for a real intent call. Being wrong cheaply is worse than being right expensively,
   because a misrouted complex request produces a bad answer the user sees.
"""

from __future__ import annotations

import re

from app.pipeline.types import (
    Ambiguity,
    Complexity,
    RequestSignals,
    RiskLevel,
    TaskType,
)

# --------------------------------------------------------------------------------------
# Lexicons. Kept as data so they can be tuned from benchmark results without touching logic.
# --------------------------------------------------------------------------------------

TASK_PATTERNS: list[tuple[TaskType, list[str], float]] = [
    (TaskType.DEBUGGING, [r"\bdebug\b", r"\bfix\b.*\b(bug|error|issue|crash)\b", r"\btraceback\b",
                          r"\bstack ?trace\b", r"\bwhy (is|does|isn't|doesn't).*\b(fail|break|error)",
                          r"\bnot working\b", r"\berror message\b",
                          # Nobody writes "please debug this". They paste the error and say it crashed.
                          r"\bcrash(es|ed|ing)?\b", r"\bthrow(s|ing)? (an? )?(error|exception)\b",
                          r"\b(Type|Value|Name|Syntax|Reference|Index|Key|Null)\s?Error\b",
                          r"\b(NullPointerException|SegmentationFault|panic:)\b",
                          r"\bundefined is not\b", r"\bcannot read propert(y|ies)\b",
                          r"\bunexpected (token|error)\b"], 0.85),
    # Verb-object distance is bounded: with an unbounded `.*`, "Write three taglines for a note-taking
    # app" matched as coding because "write" and "app" appeared in the same sentence.
    (TaskType.CODING, [r"\b(write|build|create|implement|code|generate)\b.{0,40}?\b(function|class|api|"
                       r"script|endpoint|component|module|program|service|cli|library)\b",
                       r"\b(write|build|create|implement)\b.{0,25}?\b(app|application)\b",
                       r"\bin (python|javascript|typescript|"
                       r"java|go|rust|c\+\+|ruby|php|swift|kotlin)\b", r"\brefactor\b", r"\bunit tests?\b"], 0.8),
    (TaskType.SOFTWARE_ARCHITECTURE, [r"\barchitect", r"\bsystem design\b", r"\bdesign (a|an|the) (system|"
                                      r"architecture|schema)\b", r"\bscalab", r"\bmicroservice"], 0.8),
    (TaskType.STRUCTURED_EXTRACTION, [r"\bextract\b", r"\bparse\b.*\b(into|as)\b", r"\bas json\b",
                                      r"\bjson (format|schema|object)\b", r"\bstructured (output|data)\b"], 0.85),
    (TaskType.CLASSIFICATION, [r"\bclassify\b", r"\bcategori[sz]e\b", r"\blabel (this|these|the)\b",
                               r"\bsentiment\b"], 0.85),
    (TaskType.SUMMARIZATION, [r"\bsummar[iy]", r"\btl;?dr\b", r"\bkey (points|takeaways)\b",
                              r"\bcondense\b", r"\bshorten\b"], 0.85),
    (TaskType.TRANSLATION, [r"\btranslate\b", r"\bin (spanish|french|german|japanese|chinese|hindi|telugu|"
                            r"arabic|portuguese|korean|italian|russian)\b"], 0.9),
    (TaskType.CAREER, [r"\bresume\b", r"\bcv\b", r"\bcover letter\b", r"\bjob application\b",
                       r"\blinkedin (profile|summary)\b"], 0.9),
    (TaskType.INTERVIEW_PREP, [r"\binterview (prep|question|practice)\b", r"\bmock interview\b"], 0.9),
    (TaskType.SEO, [r"\bseo\b", r"\bkeyword research\b", r"\bmeta description\b", r"\bserp\b"], 0.9),
    # Explicit marketing artefacts score above the generic coding verb-object pattern: "write taglines
    # for an app" is a copywriting request that happens to mention software.
    (TaskType.MARKETING, [r"\btaglines?\b", r"\bslogans?\b", r"\bad copy\b", r"\bheadlines? for\b",
                          r"\bvalue proposition\b", r"\bcall to action\b"], 0.9),
    (TaskType.MARKETING, [r"\b(landing|sales) page\b", r"\bmarketing\b", r"\bcampaign\b",
                          r"\bpositioning\b", r"\bbrand voice\b"], 0.8),
    # Deliberately does NOT match a bare "the report": in ordinary prose ("send me the report") that is
    # a passing reference, not a request to analyse an attachment. Requires a demonstrative/attachment
    # word, or an explicit analysis verb.
    (TaskType.DOCUMENT_ANALYSIS, [r"\b(this|these|attached|uploaded) (pdf|document|doc|file|report|contract)\b",
                                  r"\banaly[sz]e .*\b(pdf|document|contract|file)\b",
                                  r"\b(summari[sz]e|review|extract from) (this|the attached) (pdf|document|file)\b"],
     0.85),
    (TaskType.IMAGE_ANALYSIS, [r"\b(this|the|attached) (image|picture|photo|screenshot|diagram)\b",
                               r"\bwhat'?s in (this|the) (image|picture|photo)\b"], 0.85),
    (TaskType.DATA_ANALYSIS, [r"\b(this|the|attached) csv\b", r"\bdataset\b", r"\bdata analysis\b",
                              r"\bpivot table\b", r"\bcorrelat", r"\bspreadsheet\b"], 0.8),
    (TaskType.MATHEMATICS, [r"\bsolve\b.*\b(equation|integral|derivative|for x)\b", r"\bcalculate\b",
                            r"\bprove that\b", r"\btheorem\b", r"\bprobability that\b",
                            # Word problems: quantities plus units plus an interrogative about a quantity.
                            r"\bat what (time|speed|rate|point|distance|angle)\b",
                            r"\bhow (long|far|fast|many|much)\b.{0,80}\?",
                            r"\d+\s?(km/h|mph|m/s|kg|litres?|liters?|metres?|meters?)\b",
                            r"\bwhat (is|are) the (sum|product|average|mean|median|remainder)\b"], 0.8),
    (TaskType.RESEARCH, [r"\bresearch\b", r"\bcompare\b.*\b(options|tools|vendors|approaches)\b",
                         r"\bliterature\b", r"\bfind (sources|evidence|studies)\b",
                         r"\bwhat are the (best|top)\b"], 0.7),
    (TaskType.PLANNING, [r"\bplan\b", r"\broadmap\b", r"\bstep[- ]by[- ]step\b", r"\bitinerary\b",
                         r"\bschedule\b", r"\bstrategy for\b"], 0.75),
    (TaskType.BRAINSTORMING, [r"\bbrainstorm\b", r"\bideas for\b", r"\bsuggest (some|a few)\b",
                              r"\bname ideas\b"], 0.8),
    (TaskType.REWRITING, [r"\brewrite\b", r"\brephrase\b", r"\bproofread\b", r"\bedit (this|my)\b",
                          r"\bmake (this|it) (shorter|longer|clearer|more)\b", r"\bimprove (this|my) (text|"
                          r"writing|email)\b"], 0.85),
    (TaskType.EDUCATION, [r"\bexplain\b", r"\bteach me\b", r"\bwhat is\b", r"\bhow does\b",
                          r"\beli5\b", r"\bin simple terms\b"], 0.6),
    (TaskType.WRITING, [r"\bwrite\b.*\b(email|essay|article|blog|post|letter|story|script|speech)\b",
                        r"\bdraft\b", r"\bcompose\b"], 0.8),
    (TaskType.CREATIVE, [r"\bpoem\b", r"\bstory about\b", r"\blyrics\b", r"\bfiction\b",
                         r"\bcreative\b"], 0.8),
    (TaskType.BUSINESS_ANALYSIS, [r"\bbusiness (plan|model|case)\b", r"\bmarket analysis\b", r"\bswot\b",
                                  r"\bfinancial (model|projection)\b", r"\bunit economics\b"], 0.8),
    (TaskType.AGENTIC, [r"\bautomate\b", r"\bagent\b.*\b(that|which) (can|will)\b", r"\bworkflow that\b"], 0.7),
]

# Requests whose answer depends on the present moment. A static model answer is not merely stale here, it is
# wrong, so this flag forces tool/search requirements downstream.
FRESHNESS_PATTERNS = [
    r"\b(today|now|currently|current|latest|recent|this (week|month|year)|right now)\b",
    r"\b(price|stock|weather|news|score)\b.*\b(of|for|in|today)\b",
    r"\bas of\b", r"\bup to date\b", r"\bwho is the (current|present)\b",
    r"\b202[6-9]\b", r"\blatest version\b",
]

TOOL_PATTERNS = [
    r"\bsearch (the )?(web|internet|online)\b", r"\blook ?up\b", r"\bbrowse\b",
    r"\brun (this|the) code\b", r"\bexecute\b", r"\bcalculate\b.*\bexactly\b",
    r"\bquery (the )?database\b", r"\bcall (the )?api\b",
]

MULTIMODAL_PATTERNS = [
    r"\b(image|picture|photo|screenshot|diagram|chart)\b",
    r"\b(pdf|docx?|spreadsheet|csv|xlsx)\b",
]

# Domains where a confidently wrong answer can hurt someone. Drives evaluation depth and uncertainty
# disclosure — never refusal.
RISK_PATTERNS: list[tuple[RiskLevel, list[str]]] = [
    (RiskLevel.HIGH, [
        r"\b(medical|diagnos|symptom|dosage|prescription|treatment|medication)\b",
        r"\b(legal advice|lawsuit|contract review|liability|court|attorney|lawyer)\b",
        # Ordinary phrasing for legal exposure. People do not write "I require legal advice"; they write
        # "should I sue". Matching only the formal register misses the requests that matter.
        r"\b(sue|suing|sued|evict|eviction|landlord|tenant rights|divorce|custody|"
        r"immigration|visa status|deportation|arrested|charged with)\b",
        r"\b(invest|tax|financial advice|portfolio|retirement fund|mortgage|bankruptcy)\b",
        r"\b(suicide|self[- ]harm|overdose)\b",
        r"\b(security|vulnerabilit|exploit|penetration test|credential)\b",
    ]),
    (RiskLevel.MEDIUM, [
        r"\b(health|diet|nutrition|exercise plan)\b",
        r"\b(hiring|firing|hr policy|termination)\b",
        r"\b(production|deploy|migration|database schema)\b",
        r"\b(compliance|gdpr|hipaa|regulation)\b",
    ]),
]

FORMAT_PATTERNS = [
    r"\bas (a |an )?(json|table|list|markdown|csv|bullet)\b",
    r"\bin (json|markdown|table|bullet points?|numbered list)\b",
    r"\bformat(ted)? as\b", r"\breturn (a |an )?(json|list|table)\b",
    r"\b(bullet points?|numbered list)\b",
]

AUDIENCE_PATTERNS = [
    r"\bfor (a|an|my) (beginner|expert|child|kid|student|manager|ceo|engineer|developer|"
    r"non[- ]technical|technical) (audience|reader)?\b",
    r"\bexplain (it )?(to|for) a\b", r"\baudience is\b", r"\bfor (5|five) year olds?\b",
    r"\beli5\b",
]

CONSTRAINT_PATTERNS = [
    r"\b(under|less than|no more than|at most|within|maximum of|max)\b\s*\d+\s*"
    r"\b(words?|characters?|chars?|sentences?|paragraphs?|lines?|pages?|bullets?)\b",
    r"\b(exactly|at least|minimum of)\b\s*\d+\b",
    r"\bmust (include|contain|have|not)\b", r"\bdon'?t (include|use|mention)\b",
    r"\bavoid\b", r"\bwithout using\b", r"\bin (formal|casual|professional|friendly) tone\b",
]

AMBIGUITY_MARKERS = [
    r"\b(something|anything|stuff|things?)\b", r"\betc\.?\b", r"\bor whatever\b",
    r"\bsome kind of\b", r"\bmaybe\b", r"\bnot sure\b", r"\bhelp me with\b",
]

# Verbs implying multi-part deliverables. Strong complexity signal: "build" implies many decisions,
# "what is" implies one.
COMPLEX_VERBS = [
    r"\b(build|design|architect|implement|develop|create)\b",
    r"\b(analy[sz]e|evaluate|assess|investigate|compare)\b",
    r"\b(plan|strategi[sz]e|optimi[sz]e|migrate|refactor)\b",
]

SIMPLE_QUESTION_STARTS = (
    "what is", "what are", "who is", "who was", "when is", "when was", "where is",
    "how many", "how much", "define", "what does", "is it", "are there", "can i", "does",
)

CODE_MARKERS = [r"```", r"\bdef \w+\(", r"\bfunction \w+\(", r"\bclass \w+", r"\bimport \w+",
               r"\bSELECT\b.*\bFROM\b", r"[{};]\s*$", r"\bconst \w+ ="]


def _matches(patterns: list[str], text: str) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def _count_matches(patterns: list[str], text: str) -> int:
    return sum(1 for p in patterns if re.search(p, text, re.IGNORECASE))


def extract_signals(raw: str) -> RequestSignals:
    """Extract deterministic signals from raw user input. Pure, free, no I/O."""
    text = raw.strip()
    lowered = text.lower()
    words = text.split()
    sig = RequestSignals(char_count=len(text), word_count=len(words))
    rules: list[str] = []

    # --- task classification ---------------------------------------------------------
    best_task, best_conf = TaskType.GENERAL_QA, 0.0
    for task, patterns, conf in TASK_PATTERNS:
        hits = _count_matches(patterns, text)
        if hits:
            # Multiple distinct pattern hits for one task raise confidence, capped so a keyword pile-up
            # never fully substitutes for real understanding.
            score = min(conf + 0.05 * (hits - 1), 0.95)
            if score > best_conf:
                best_task, best_conf = task, score
                rules.append(f"task:{task.value}")
    sig.likely_task = best_task

    # --- structural signals ----------------------------------------------------------
    sig.is_question = text.endswith("?") or lowered.startswith(
        ("what", "who", "when", "where", "why", "how", "is ", "are ", "can ", "does ", "do ")
    )
    sig.is_imperative = bool(re.match(r"^(write|make|build|create|give|show|explain|list|find|help)", lowered))
    sig.contains_code = _matches(CODE_MARKERS, text)
    sig.has_explicit_format = _matches(FORMAT_PATTERNS, text)
    sig.has_explicit_audience = _matches(AUDIENCE_PATTERNS, text)
    sig.has_constraints = _matches(CONSTRAINT_PATTERNS, text)
    sig.needs_current_info = _matches(FRESHNESS_PATTERNS, text)
    sig.needs_tools = sig.needs_current_info or _matches(TOOL_PATTERNS, text)
    sig.needs_multimodal = _matches(MULTIMODAL_PATTERNS, text)

    if sig.has_explicit_format:
        rules.append("explicit_format")
    if sig.has_constraints:
        rules.append("constraints")
    if sig.needs_current_info:
        rules.append("freshness_required")

    # --- risk -------------------------------------------------------------------------
    for level, patterns in RISK_PATTERNS:
        if _matches(patterns, text):
            sig.risk = level
            rules.append(f"risk:{level.value}")
            break

    # --- ambiguity before complexity --------------------------------------------------
    # Ambiguity does not depend on complexity, but complexity reads the `underspecified_open_task` rule
    # that ambiguity scoring emits, so this order matters.
    sig.ambiguity = _score_ambiguity(text, lowered, words, sig, rules)
    sig.complexity = _score_complexity(text, lowered, words, sig, rules)

    # --- confidence -------------------------------------------------------------------
    sig.confidence = _score_confidence(best_conf, sig)
    sig.matched_rules = rules
    return sig


def _score_complexity(
    text: str, lowered: str, words: list[str], sig: RequestSignals, rules: list[str]
) -> Complexity:
    score = 0

    # Length is a weak but real signal; weighted low so a long simple question is not over-classified.
    if len(words) > 120:
        score += 2
    elif len(words) > 50:
        score += 1
    elif len(words) < 8:
        score -= 1

    complex_verb_hits = _count_matches(COMPLEX_VERBS, text)
    score += min(complex_verb_hits, 2)

    if lowered.startswith(SIMPLE_QUESTION_STARTS) and len(words) < 25:
        score -= 2
        rules.append("simple_question_form")

    # Conjoined deliverables ("X and also Y", numbered lists) mean more than one thing is being asked for.
    if re.search(r"\band (also|then)\b", lowered) or len(re.findall(r"^\s*[-*\d]+[.)]\s", text, re.M)) >= 3:
        score += 1
        rules.append("multi_part")

    if sig.likely_task in {
        TaskType.SOFTWARE_ARCHITECTURE, TaskType.RESEARCH, TaskType.BUSINESS_ANALYSIS,
        TaskType.AGENTIC, TaskType.PLANNING,
    }:
        score += 2
    elif sig.likely_task in {TaskType.CODING, TaskType.DEBUGGING, TaskType.DATA_ANALYSIS}:
        score += 1
    elif sig.likely_task in {TaskType.TRANSLATION, TaskType.CLASSIFICATION, TaskType.SUMMARIZATION}:
        score -= 1

    if sig.risk is RiskLevel.HIGH:
        score += 1

    # An open-ended build/create request is never genuinely simple, however few words it uses.
    # "build a landing page for my startup" is 6 words and a week of decisions; the brevity is missing
    # specification, not absent work. Counteracts the short-input penalty above.
    if "underspecified_open_task" in rules and complex_verb_hits:
        score += 2
        rules.append("open_ended_build")

    if score <= -2:
        return Complexity.TRIVIAL
    if score <= 0:
        return Complexity.LOW
    if score <= 2:
        return Complexity.MEDIUM
    if score <= 4:
        return Complexity.HIGH
    return Complexity.VERY_HIGH


def _score_ambiguity(
    text: str, lowered: str, words: list[str], sig: RequestSignals, rules: list[str]
) -> Ambiguity:
    score = 0
    marker_hits = _count_matches(AMBIGUITY_MARKERS, text)
    score += min(marker_hits, 2)

    # Very short imperatives for open-ended creative/build work are the classic under-specified request:
    # "build me a website" has no audience, no scope, no constraints.
    if len(words) < 12 and sig.likely_task in {
        TaskType.CODING, TaskType.WRITING, TaskType.MARKETING, TaskType.CREATIVE,
        TaskType.SOFTWARE_ARCHITECTURE, TaskType.PLANNING, TaskType.BUSINESS_ANALYSIS,
        # CAREER belongs here: "make me a resume" cannot be done well without the person's actual
        # history, so the request is under-specified no matter how confidently we classify it.
        TaskType.CAREER, TaskType.INTERVIEW_PREP, TaskType.SEO,
    }:
        score += 2
        rules.append("underspecified_open_task")

    # Explicit specification reduces ambiguity — this is what the user actually telling us looks like.
    if sig.has_explicit_format:
        score -= 1
    if sig.has_explicit_audience:
        score -= 1
    if sig.has_constraints:
        score -= 1
    if sig.contains_code:
        score -= 1
    if len(words) > 60:
        score -= 1  # long requests usually carry their own context

    if lowered.startswith(SIMPLE_QUESTION_STARTS):
        score -= 1

    if score <= -2:
        return Ambiguity.NONE
    if score <= 0:
        return Ambiguity.LOW
    if score <= 2:
        return Ambiguity.MEDIUM
    return Ambiguity.HIGH


def _score_confidence(task_conf: float, sig: RequestSignals) -> float:
    """Confidence that these signals are a sound basis for skipping LLM intent analysis.

    Deliberately pessimistic. It drops on every indicator that the request has hidden depth, because the
    cost of a false "this is simple" is a bad answer, while the cost of a false "this is complex" is a few
    cents.
    """
    conf = task_conf if task_conf > 0 else 0.35

    if sig.ambiguity is Ambiguity.HIGH:
        conf -= 0.30
    elif sig.ambiguity is Ambiguity.MEDIUM:
        conf -= 0.12

    if sig.complexity.rank >= Complexity.HIGH.rank:
        conf -= 0.20
    elif sig.complexity is Complexity.TRIVIAL:
        conf += 0.10

    if sig.risk is RiskLevel.HIGH:
        conf -= 0.25  # never fast-path a high-risk request on heuristics alone
    elif sig.risk is RiskLevel.MEDIUM:
        conf -= 0.10

    if sig.needs_tools or sig.needs_multimodal:
        conf -= 0.15

    # Explicit user specification is the strongest evidence we understand the request: they told us.
    if sig.has_explicit_format:
        conf += 0.08
    if sig.has_constraints:
        conf += 0.05
    if sig.has_explicit_audience:
        conf += 0.05

    if sig.word_count < 4:
        conf -= 0.20  # too little to go on

    return max(0.0, min(1.0, round(conf, 3)))
