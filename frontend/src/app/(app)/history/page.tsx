"use client";

import { useEffect, useState } from "react";
import * as api from "@/lib/api";
import type { ExecutionSummary } from "@/lib/types";
import { Badge, Card, EmptyState, Spinner, formatMs, formatUsd } from "@/components/ui";

export default function HistoryPage() {
  const [executions, setExecutions] = useState<ExecutionSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listExecutions()
      .then(setExecutions)
      .catch(() => setError("Could not load execution history."));
  }, []);

  return (
    <div className="mx-auto max-w-4xl px-6 py-8 sm:px-10">
      <h1 className="text-2xl font-semibold tracking-tight">History</h1>
      <p className="mt-1 text-sm text-[var(--text-muted)]">
        Every execution, with its cost, model and confidence. Raw content only appears here if your
        workspace has opted into retaining it (Settings → Privacy).
      </p>

      {error && <p className="mt-6 text-sm text-[var(--danger)]">{error}</p>}
      {!executions && !error && (
        <div className="mt-10 flex justify-center">
          <Spinner className="text-[var(--text-faint)]" />
        </div>
      )}
      {executions && executions.length === 0 && (
        <div className="mt-6">
          <EmptyState
            title="No executions yet"
            description="Run something in the Playground and it will show up here."
          />
        </div>
      )}

      <div className="mt-6 space-y-2">
        {executions?.map((e) => (
          <Card key={e.id} className="p-3.5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm text-[var(--text)]">{e.preview}</p>
                <p className="mono mt-0.5 text-xs text-[var(--text-faint)]">
                  {new Date(e.created_at).toLocaleString()}
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <Badge>{e.mode}</Badge>
                <Badge tone={e.status === "ok" ? "ok" : "danger"}>{e.status}</Badge>
                <span className="mono text-xs text-[var(--text-muted)]">{formatUsd(e.cost_usd)}</span>
                <span className="mono text-xs text-[var(--text-faint)]">{formatMs(e.latency_ms)}</span>
              </div>
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
