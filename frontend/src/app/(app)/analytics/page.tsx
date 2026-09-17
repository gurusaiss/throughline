"use client";

import { useEffect, useState } from "react";
import * as api from "@/lib/api";
import type { AnalyticsResponse } from "@/lib/types";
import { Card, Spinner } from "@/components/ui";

export default function AnalyticsPage() {
  const [data, setData] = useState<AnalyticsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .getAnalytics(30)
      .then(setData)
      .catch(() => setError("Could not load analytics."));
  }, []);

  if (error) return <p className="p-8 text-sm text-[var(--danger)]">{error}</p>;
  if (!data)
    return (
      <div className="flex justify-center py-16">
        <Spinner className="text-[var(--text-faint)]" />
      </div>
    );

  const budgetPct = data.budget_limit_usd > 0 ? (data.budget_spent_usd / data.budget_limit_usd) * 100 : 0;

  return (
    <div className="mx-auto max-w-4xl px-6 py-8 sm:px-10">
      <h1 className="text-2xl font-semibold tracking-tight">Analytics</h1>
      <p className="mt-1 text-sm text-[var(--text-muted)]">Last {data.period_days} days.</p>

      <div className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Metric label="Executions" value={String(data.executions)} />
        <Metric label="Total cost" value={`$${data.total_cost_usd.toFixed(4)}`} />
        <Metric label="Avg latency" value={`${data.avg_latency_ms}ms`} />
        <Metric label="Avg model calls" value={data.avg_llm_calls.toFixed(2)} />
      </div>

      <Card className="mt-6 p-4">
        <div className="mb-2 flex items-center justify-between">
          <p className="text-sm font-medium text-[var(--text)]">Monthly budget</p>
          <span className="mono text-xs text-[var(--text-muted)]">
            ${data.budget_spent_usd.toFixed(4)} / ${data.budget_limit_usd.toFixed(2)}
          </span>
        </div>
        <div className="h-2 overflow-hidden rounded-full" style={{ background: "var(--bg)" }}>
          <div
            className="h-full rounded-full"
            style={{
              width: `${Math.min(100, budgetPct)}%`,
              background: budgetPct > 90 ? "var(--danger)" : budgetPct > 70 ? "var(--warn)" : "var(--ok)",
            }}
          />
        </div>
      </Card>

      <Card className="mt-4 p-4">
        <p className="mb-2 text-sm font-medium text-[var(--text)]">Cost breakdown</p>
        <div className="flex items-center gap-4 text-xs text-[var(--text-muted)]">
          <span>Generation: ${data.generation_cost_usd.toFixed(4)}</span>
          <span>Orchestration: ${data.orchestration_cost_usd.toFixed(4)}</span>
          <span className="mono">({Math.round(data.orchestration_ratio * 100)}% overhead)</span>
        </div>
      </Card>

      <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Distribution title="By mode" data={data.mode_distribution} />
        <Distribution title="By task" data={data.task_distribution} />
        <Distribution title="By model" data={data.model_distribution} />
      </div>

      <Card className="mt-4 p-4">
        <p className="text-sm font-medium text-[var(--text)]">Feedback</p>
        <p className="mt-1 text-sm text-[var(--text-muted)]">
          👍 {data.feedback_positive} · 👎 {data.feedback_negative}
        </p>
      </Card>

      <p className="mt-6 text-xs text-[var(--text-faint)]">{data.note}</p>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <Card className="p-4">
      <p className="mono text-xl font-semibold text-[var(--text)]">{value}</p>
      <p className="text-xs text-[var(--text-muted)]">{label}</p>
    </Card>
  );
}

function Distribution({ title, data }: { title: string; data: Record<string, number> }) {
  const entries = Object.entries(data).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...entries.map(([, v]) => v));
  return (
    <Card className="p-4">
      <p className="mb-2 text-sm font-medium text-[var(--text)]">{title}</p>
      {entries.length === 0 && <p className="text-xs text-[var(--text-faint)]">No data yet.</p>}
      <div className="space-y-1.5">
        {entries.map(([key, value]) => (
          <div key={key}>
            <div className="mb-0.5 flex justify-between text-xs text-[var(--text-muted)]">
              <span className="truncate">{key}</span>
              <span className="mono">{value}</span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full" style={{ background: "var(--bg)" }}>
              <div
                className="h-full rounded-full"
                style={{ width: `${(value / max) * 100}%`, background: "var(--accent)" }}
              />
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}
