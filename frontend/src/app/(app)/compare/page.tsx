"use client";

import { useEffect, useState } from "react";
import * as api from "@/lib/api";
import { ApiError } from "@/lib/api";
import type { CompareResponse, ModelResponse } from "@/lib/types";
import { Badge, Button, Card, ErrorBanner, Spinner, TextArea } from "@/components/ui";

type Mode = "raw_vs_optimized" | "models";

export default function ComparePage() {
  const [mode, setMode] = useState<Mode>("raw_vs_optimized");
  const [input, setInput] = useState("");
  const [models, setModels] = useState<ModelResponse[] | null>(null);
  const [selectedModels, setSelectedModels] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<CompareResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (mode === "models" && models === null) {
      api
        .listModels()
        .then((list) => setModels(list.filter((m) => m.available && m.configured)))
        .catch(() => setError("Could not load the model list."));
    }
  }, [mode, models]);

  function toggleModel(id: string) {
    setSelectedModels((prev) => {
      if (prev.includes(id)) return prev.filter((m) => m !== id);
      if (prev.length >= 4) return prev;
      return [...prev, id];
    });
  }

  async function onRun() {
    if (!input.trim() || busy) return;
    if (mode === "models" && selectedModels.length < 2) {
      setError("Pick at least two models to compare.");
      return;
    }
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const res = await api.compare(input, mode, mode === "models" ? selectedModels : []);
      setResult(res);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reach the server.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-5xl px-6 py-8 sm:px-10">
      <h1 className="text-2xl font-semibold tracking-tight">Compare</h1>
      <p className="mt-1 text-sm text-[var(--text-muted)]">
        See the same request handled differently, side by side. This is the honest way to judge whether an
        approach actually helps for your kind of request — one run is one sample, not proof.
      </p>

      <div className="mt-4 flex gap-2">
        <ModeTab active={mode === "raw_vs_optimized"} onClick={() => setMode("raw_vs_optimized")}>
          Raw vs optimised
        </ModeTab>
        <ModeTab active={mode === "models"} onClick={() => setMode("models")}>
          Model vs model
        </ModeTab>
      </div>

      <Card className="mt-4 p-4">
        <TextArea
          rows={3}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="e.g. write a professional email asking for an internship"
        />

        {mode === "models" && (
          <div className="mt-3">
            <p className="mb-2 text-xs font-medium uppercase tracking-wide text-[var(--text-muted)]">
              Pick 2–4 models
            </p>
            {!models && (
              <div className="flex justify-center py-4">
                <Spinner className="text-[var(--text-faint)]" />
              </div>
            )}
            {models && models.length === 0 && (
              <p className="text-sm text-[var(--text-faint)]">
                No configured models available. Add a provider key to compare models.
              </p>
            )}
            {models && models.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {models.map((m) => {
                  const active = selectedModels.includes(m.id);
                  return (
                    <button
                      key={m.id}
                      type="button"
                      onClick={() => toggleModel(m.id)}
                      className="rounded-full border px-3 py-1.5 text-xs transition-colors"
                      style={{
                        borderColor: active ? "var(--accent)" : "var(--border-strong)",
                        background: active ? "var(--accent-tint)" : "transparent",
                        color: active ? "var(--accent-strong)" : "var(--text-muted)",
                      }}
                    >
                      {m.display_name}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        )}

        <div className="mt-3 flex justify-end">
          <Button onClick={onRun} disabled={busy || !input.trim()}>
            {busy ? (
              <>
                <Spinner /> Running…
              </>
            ) : (
              "Run comparison"
            )}
          </Button>
        </div>
      </Card>

      {error && (
        <div className="mt-6">
          <ErrorBanner message={error} />
        </div>
      )}

      {result && (
        <div className="animate-fade-in mt-6 space-y-4">
          <Card className="p-4">
            <p className="text-sm font-medium text-[var(--text)]">{result.verdict}</p>
            <p className="mt-1 text-xs text-[var(--text-faint)]">{result.caveat}</p>
          </Card>

          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            {result.arms.map((arm) => (
              <Card key={arm.label} className="p-4">
                <div className="mb-3 flex items-center justify-between">
                  <Badge tone={arm.label === "optimized" ? "accent" : "neutral"}>{arm.label}</Badge>
                  <span className="mono text-xs text-[var(--text-faint)]">{arm.model_id}</span>
                </div>
                <div className="mb-3 whitespace-pre-wrap text-sm leading-relaxed text-[var(--text)]">
                  {arm.output || <span className="text-[var(--text-faint)]">No content returned.</span>}
                </div>
                <div className="flex flex-wrap gap-3 border-t pt-3 text-xs text-[var(--text-muted)]" style={{ borderColor: "var(--border)" }}>
                  <span>
                    checks: <span className="mono text-[var(--ok)]">{arm.checks_passed} pass</span>
                    {arm.checks_failed > 0 && (
                      <span className="mono text-[var(--danger)]"> · {arm.checks_failed} fail</span>
                    )}
                  </span>
                  <span className="mono">${arm.cost_usd.toFixed(6)}</span>
                  <span className="mono">{arm.latency_ms}ms</span>
                </div>
              </Card>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function ModeTab({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-[var(--radius-md)] px-3 py-1.5 text-sm font-medium transition-colors"
      style={{
        background: active ? "var(--accent-tint)" : "transparent",
        color: active ? "var(--accent-strong)" : "var(--text-muted)",
      }}
    >
      {children}
    </button>
  );
}
