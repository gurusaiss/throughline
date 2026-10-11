"use client";

import { useState } from "react";
import Link from "next/link";
import * as api from "@/lib/api";
import { ApiError } from "@/lib/api";
import type { ExecutionResponse, OrchestrationMode } from "@/lib/types";
import { Button, Card, ErrorBanner, Input, Spinner, TextArea, WarningBanner } from "@/components/ui";
import {
  CostPanel,
  EvaluationPanel,
  ExecutionSteps,
  FeedbackButtons,
  WhyThisPrompt,
} from "@/components/execution-view";

const MODES: { value: OrchestrationMode; label: string; hint: string }[] = [
  { value: "auto", label: "Auto", hint: "system decides" },
  { value: "fast", label: "Fast", hint: "minimal transform" },
  { value: "balanced", label: "Balanced", hint: "optimise + 1 model" },
  { value: "deep", label: "Deep", hint: "compare candidates" },
];

const EXAMPLES = [
  "Write a professional email asking for an internship",
  "Design a scalable microservice architecture for a video streaming platform",
  "Extract the name, email and phone from this text as JSON: Ada Lovelace, ada@example.com, +44 20 7946 0958",
  "What dosage of ibuprofen should I take for a headache?",
];

export default function PlaygroundPage() {
  const [input, setInput] = useState("");
  const [mode, setMode] = useState<OrchestrationMode>("auto");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ExecutionResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rated, setRated] = useState<"up" | "down" | null>(null);
  const [saveAsPrompt, setSaveAsPrompt] = useState(false);
  const [promptName, setPromptName] = useState("");

  async function onRun() {
    if (!input.trim() || busy) return;
    setBusy(true);
    setError(null);
    setResult(null);
    setRated(null);
    try {
      const res = await api.execute({
        input,
        mode,
        save_as_prompt: saveAsPrompt && promptName.trim() ? promptName.trim() : null,
      });
      setResult(res);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message + (err.detail ? ` — ${err.detail}` : ""));
      } else {
        setError("Could not reach the server. Is the backend running?");
      }
    } finally {
      setBusy(false);
    }
  }

  async function onRate(rating: "up" | "down") {
    if (!result) return;
    setRated(rating);
    try {
      await api.submitFeedback({ execution_id: result.execution_id, rating });
    } catch {
      // Feedback is best-effort; a failed submission should not disrupt the reading experience.
    }
  }

  return (
    <div className="mx-auto max-w-3xl px-6 py-8 sm:px-10">
      <h1 className="text-2xl font-semibold tracking-tight">What do you want to accomplish?</h1>
      <p className="mt-1 text-sm text-[var(--text-muted)]">
        Describe it in plain language. Throughline decides how much optimisation it deserves.
      </p>

      <Card className="mt-6 p-4">
        <TextArea
          rows={5}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="e.g. build a landing page for my startup"
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) onRun();
          }}
        />
        <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label="Orchestration mode">
            {MODES.map((m) => (
              <button
                key={m.value}
                role="radio"
                aria-checked={mode === m.value}
                onClick={() => setMode(m.value)}
                className="rounded-[var(--radius-sm)] border px-3 py-1.5 text-xs font-medium transition-[background-color,color,border-color,transform] duration-[var(--dur-fast)] ease-[var(--ease-press)] active:translate-y-px"
                style={{
                  background: mode === m.value ? "var(--accent)" : "transparent",
                  color: mode === m.value ? "white" : "var(--text-muted)",
                  borderColor: mode === m.value ? "var(--accent)" : "var(--border-strong)",
                }}
                title={m.hint}
              >
                {m.label}
              </button>
            ))}
          </div>
          <Button onClick={onRun} disabled={busy || !input.trim()}>
            {busy ? (
              <>
                <Spinner /> Working…
              </>
            ) : (
              "Optimize & Run"
            )}
          </Button>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-2 border-t pt-3" style={{ borderColor: "var(--border)" }}>
          <label className="flex items-center gap-2 text-xs text-[var(--text-muted)]">
            <input
              type="checkbox"
              checked={saveAsPrompt}
              onChange={(e) => setSaveAsPrompt(e.target.checked)}
            />
            Save the compiled prompt to the library
          </label>
          {saveAsPrompt && (
            <Input
              value={promptName}
              onChange={(e) => setPromptName(e.target.value)}
              placeholder="prompt name"
              className="max-w-[220px] py-1.5 text-xs"
            />
          )}
        </div>
      </Card>

      {!result && !busy && !error && (
        <div className="mt-6">
          <p className="mb-2 text-xs font-medium uppercase tracking-wide text-[var(--text-faint)]">
            Try one
          </p>
          <div className="flex flex-wrap gap-2">
            {EXAMPLES.map((ex) => (
              <button
                key={ex}
                onClick={() => setInput(ex)}
                className="rounded-[var(--radius-md)] border px-3 py-1.5 text-left text-xs text-[var(--text-muted)] transition-colors duration-[var(--dur-fast)] hover:border-[var(--accent)] hover:text-[var(--text)]"
                style={{ borderColor: "var(--border)" }}
              >
                {ex.length > 60 ? ex.slice(0, 57) + "…" : ex}
              </button>
            ))}
          </div>
        </div>
      )}

      {error && (
        <div className="mt-6">
          <ErrorBanner message={error} />
        </div>
      )}

      {result && (
        <div className="animate-fade-in mt-8 space-y-5">
          {result.status !== "ok" ? (
            <ErrorBanner message={result.error ?? "The request could not be completed."} />
          ) : (
            <>
              <Card className="p-4">
                <p className="mb-2 text-xs font-medium uppercase tracking-wide text-[var(--text-faint)]">
                  Execution
                </p>
                <ExecutionSteps result={result} />
              </Card>

              <Card className="p-5">
                <div className="flex items-center justify-between">
                  <p className="text-xs font-medium uppercase tracking-wide text-[var(--text-faint)]">
                    Answer <span className="mono">(generated by AI)</span>
                  </p>
                  {result.candidates.some((c) => c.from_cache) && (
                    <span
                      className="mono border-l-2 pl-1.5 text-[11px]"
                      style={{ borderColor: "var(--border-strong)", color: "var(--text-faint)" }}
                      title="An identical request was answered recently; this response was served from cache rather than generated again."
                    >
                      served from cache
                    </span>
                  )}
                </div>
                <div className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-[var(--text)]">
                  {result.output || <span className="text-[var(--text-faint)]">No content returned.</span>}
                </div>
                <div className="mt-4 border-t pt-4" style={{ borderColor: "var(--border)" }}>
                  <FeedbackButtons onRate={onRate} rated={rated} />
                </div>
                {result.saved_prompt_id && (
                  <p className="mt-2 text-xs text-[var(--ok)]">
                    Saved to the{" "}
                    <Link href="/prompts" className="underline">
                      prompt library
                    </Link>
                    .
                  </p>
                )}
              </Card>

              {result.warnings.length > 0 && (
                <div className="space-y-2">
                  {result.warnings.map((w, i) => (
                    <WarningBanner key={i} message={w} />
                  ))}
                </div>
              )}

              <Card className="p-4">
                <p className="mb-3 text-xs font-medium uppercase tracking-wide text-[var(--text-faint)]">
                  Evaluation
                </p>
                <EvaluationPanel result={result} />
              </Card>

              <Card className="p-4">
                <p className="mb-3 text-xs font-medium uppercase tracking-wide text-[var(--text-faint)]">
                  Cost & performance
                </p>
                <CostPanel result={result} />
              </Card>

              <Card className="p-4">
                <WhyThisPrompt result={result} />
              </Card>
            </>
          )}
        </div>
      )}
    </div>
  );
}
