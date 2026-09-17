"use client";

import { useState } from "react";
import * as api from "@/lib/api";
import { ApiError } from "@/lib/api";
import type { CompareResponse } from "@/lib/types";
import { Badge, Button, Card, ErrorBanner, Spinner, TextArea } from "@/components/ui";

export default function ComparePage() {
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<CompareResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function onRun() {
    if (!input.trim() || busy) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const res = await api.compare(input, "raw_vs_optimized");
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
        See the same request handled raw versus fully optimised, side by side. This is the honest way to
        judge whether optimisation actually helps for your kind of request — one run is one sample, not proof.
      </p>

      <Card className="mt-6 p-4">
        <TextArea
          rows={3}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="e.g. write a professional email asking for an internship"
        />
        <div className="mt-3 flex justify-end">
          <Button onClick={onRun} disabled={busy || !input.trim()}>
            {busy ? (
              <>
                <Spinner /> Running both arms…
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
