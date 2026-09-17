"use client";

// Renders the safe, high-level execution steps (PRODUCT §6 / ARCHITECTURE): what was decided and why,
// never the model's internal chain-of-thought. Steps are derived from the real response — nothing here
// is animated filler standing in for progress we don't actually know.

import { useState } from "react";
import type { ExecutionResponse } from "@/lib/types";
import { Badge, Button, ConfidenceBadge, formatMs, formatUsd } from "./ui";

function StepRow({
  label,
  detail,
  done = true,
}: {
  label: string;
  detail: string;
  done?: boolean;
}) {
  return (
    <div className="flex items-start gap-3 py-2">
      <div
        className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[10px] font-bold"
        style={{
          background: done ? "var(--ok-tint)" : "var(--bg)",
          color: done ? "var(--ok)" : "var(--text-faint)",
          border: done ? "none" : "1px solid var(--border-strong)",
        }}
      >
        {done ? "✓" : ""}
      </div>
      <div className="min-w-0">
        <p className="text-sm font-medium text-[var(--text)]">{label}</p>
        <p className="text-xs text-[var(--text-muted)]">{detail}</p>
      </div>
    </div>
  );
}

export function ExecutionSteps({ result }: { result: ExecutionResponse }) {
  const anyFromCache = result.candidates.some((c) => c.from_cache);
  const steps: { label: string; detail: string }[] = [
    {
      label: "Understanding request",
      detail: `${result.why.task_type} · ${result.why.complexity} complexity · ${result.why.ambiguity} ambiguity`,
    },
    {
      label: "Optimising instructions",
      detail:
        result.why.applied.length > 0
          ? `Applied ${result.why.applied.length} technique${result.why.applied.length === 1 ? "" : "s"}`
          : "Passed through with minimal transformation",
    },
  ];
  if (result.routing) {
    steps.push({
      label: "Selecting model",
      detail: `${result.routing.strategy} · ${result.routing.selections.map((s) => s.model_id).join(", ")}`,
    });
  }
  steps.push({
    label: "Generating",
    // An identical request within the cache window is served from cache rather than re-run — that is
    // why the call count and cost can legitimately read zero, and it must say so rather than look like
    // an unexplained free answer (PRODUCT §7: every signal ships with its provenance).
    detail: anyFromCache
      ? "Served from cache — an identical request was answered recently, so no model call was made"
      : `${result.cost.llm_calls} model call${result.cost.llm_calls === 1 ? "" : "s"}`,
  });
  if (result.evaluation) {
    steps.push({
      label: "Evaluating",
      detail:
        result.evaluation.status === "complete"
          ? "Checked automatically and judged against a rubric"
          : result.evaluation.status === "deterministic_only"
            ? "Checked automatically (format/constraints only)"
            : "Unavailable for this response",
    });
  }
  steps.push({ label: "Preparing final answer", detail: result.why.mode_reason });

  return (
    <div className="divide-y" style={{ borderColor: "var(--border)" }}>
      {steps.map((s) => (
        <StepRow key={s.label} label={s.label} detail={s.detail} />
      ))}
    </div>
  );
}

export function WhyThisPrompt({ result }: { result: ExecutionResponse }) {
  const [open, setOpen] = useState(false);
  const { why } = result;
  return (
    <div>
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between text-left text-sm font-medium text-[var(--text)]"
      >
        <span>Why this prompt?</span>
        <span className="mono text-xs text-[var(--text-faint)]">{open ? "hide" : "show"}</span>
      </button>
      {open && (
        <div className="animate-fade-in mt-3 space-y-3">
          <p className="text-sm text-[var(--text-muted)]">{why.rationale}</p>
          {why.applied.length > 0 && (
            <ul className="space-y-2">
              {why.applied.map((t) => (
                <li key={t.technique} className="text-sm">
                  <Badge tone="accent" className="mr-2">
                    {t.technique.replace(/_/g, " ")}
                  </Badge>
                  <span className="text-[var(--text-muted)]">{t.reason}</span>
                </li>
              ))}
            </ul>
          )}
          {why.skipped.length > 0 && (
            <details className="text-xs text-[var(--text-faint)]">
              <summary className="cursor-pointer">
                {why.skipped.length} technique{why.skipped.length === 1 ? "" : "s"} deliberately not applied
              </summary>
              <ul className="mt-1.5 space-y-1 pl-4">
                {why.skipped.map((s) => (
                  <li key={s} className="list-disc">
                    {s}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
    </div>
  );
}

export function CostPanel({ result }: { result: ExecutionResponse }) {
  const { cost } = result;
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <Stat label="Total cost" value={formatUsd(cost.total_usd)} />
      <Stat label="Model calls" value={String(cost.llm_calls)} />
      <Stat label="Tokens in/out" value={`${cost.input_tokens}/${cost.output_tokens}`} />
      <Stat
        label="Orchestration share"
        value={`${Math.round(cost.orchestration_ratio * 100)}%`}
        hint="spend on deciding, not answering"
      />
      {cost.includes_unverified_pricing && (
        <p className="col-span-full text-xs text-[var(--warn)]">
          Includes unverified pricing — treat cost as indicative only.
        </p>
      )}
    </div>
  );
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div>
      <p className="mono text-lg font-semibold text-[var(--text)]">{value}</p>
      <p className="text-xs text-[var(--text-muted)]">{label}</p>
      {hint && <p className="text-[10px] text-[var(--text-faint)]">{hint}</p>}
    </div>
  );
}

export function EvaluationPanel({ result }: { result: ExecutionResponse }) {
  const ev = result.evaluation;
  if (!ev) return null;
  const selected = result.candidates.find((c) => c.id === ev.selected_candidate_id);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <ConfidenceBadge level={result.confidence} />
        <Badge tone={ev.status === "complete" ? "ok" : "neutral"}>{ev.status.replace(/_/g, " ")}</Badge>
        {ev.mitigations?.self_preference_risk && (
          <Badge tone="warn">judge shares a family with a candidate</Badge>
        )}
      </div>
      <p className="text-xs text-[var(--text-faint)]">{result.confidence_note}</p>
      {ev.selection_reason && (
        <p className="text-sm text-[var(--text-muted)]">{ev.selection_reason}</p>
      )}
      {selected && selected.checks.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {selected.checks.map((c) => (
            <Badge key={c.name} tone={c.status === "pass" ? "ok" : c.status === "fail" ? "danger" : "neutral"}>
              {c.name}
            </Badge>
          ))}
        </div>
      )}
      {result.candidates.length > 1 && (
        <details className="text-xs text-[var(--text-muted)]">
          <summary className="cursor-pointer font-medium text-[var(--text)]">
            Compare all {result.candidates.length} candidates
          </summary>
          <div className="mt-2 space-y-2">
            {result.candidates.map((c) => (
              <div
                key={c.id}
                className="rounded-[var(--radius-md)] border p-2.5"
                style={{
                  borderColor: c.id === ev.selected_candidate_id ? "var(--accent)" : "var(--border)",
                }}
              >
                <div className="mb-1 flex items-center justify-between">
                  <span className="mono text-xs font-medium">{c.model_id}</span>
                  <span className="mono text-xs text-[var(--text-faint)]">
                    {c.overall !== null ? `${Math.round(c.overall * 100)}%` : "—"} · {formatUsd(c.cost_usd)} ·{" "}
                    {formatMs(c.latency_ms)}
                  </span>
                </div>
                <p className="line-clamp-3 text-xs text-[var(--text-muted)]">{c.text}</p>
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

export function FeedbackButtons({
  onRate,
  rated,
}: {
  onRate: (rating: "up" | "down") => void;
  rated: "up" | "down" | null;
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-[var(--text-faint)]">Was this useful?</span>
      <Button
        variant={rated === "up" ? "primary" : "secondary"}
        size="sm"
        onClick={() => onRate("up")}
        aria-pressed={rated === "up"}
      >
        👍
      </Button>
      <Button
        variant={rated === "down" ? "primary" : "secondary"}
        size="sm"
        onClick={() => onRate("down")}
        aria-pressed={rated === "down"}
      >
        👎
      </Button>
    </div>
  );
}
