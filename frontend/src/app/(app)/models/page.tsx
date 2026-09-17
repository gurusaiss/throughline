"use client";

import { useEffect, useState } from "react";
import * as api from "@/lib/api";
import type { ModelResponse } from "@/lib/types";
import { Badge, Card, EmptyState, Spinner } from "@/components/ui";

export default function ModelsPage() {
  const [models, setModels] = useState<ModelResponse[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listModels()
      .then(setModels)
      .catch(() => setError("Could not load the model registry."));
  }, []);

  return (
    <div className="mx-auto max-w-5xl px-6 py-8 sm:px-10">
      <h1 className="text-2xl font-semibold tracking-tight">Model registry</h1>
      <p className="mt-1 text-sm text-[var(--text-muted)]">
        Pricing is versioned data, not a hard-coded constant — every price carries where it came from and
        when it was last checked. Unverified or stale prices are flagged, not hidden.
      </p>

      {error && <p className="mt-6 text-sm text-[var(--danger)]">{error}</p>}
      {!models && !error && (
        <div className="mt-10 flex justify-center">
          <Spinner className="text-[var(--text-faint)]" />
        </div>
      )}

      {models && models.length === 0 && (
        <div className="mt-6">
          <EmptyState title="No models in the catalogue" />
        </div>
      )}

      {models && models.length > 0 && (
        <div className="mt-6 space-y-3">
          {models.map((m) => (
            <Card key={m.id} className="p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="flex items-center gap-2">
                    <p className="font-medium text-[var(--text)]">{m.display_name}</p>
                    <Badge>{m.provider}</Badge>
                    <Badge>{m.tier}</Badge>
                    {!m.configured && <Badge tone="warn">no API key configured</Badge>}
                    {!m.available && <Badge tone="neutral">unavailable</Badge>}
                  </div>
                  <p className="mono mt-1 text-xs text-[var(--text-faint)]">{m.id}</p>
                </div>
                <div className="text-right">
                  <p className="mono text-sm text-[var(--text)]">
                    ${m.pricing.input_per_mtok.toFixed(2)} / ${m.pricing.output_per_mtok.toFixed(2)}{" "}
                    <span className="text-[var(--text-faint)]">per MTok</span>
                  </p>
                  <div className="mt-1 flex items-center justify-end gap-1.5">
                    <Badge tone={m.pricing.verified ? "ok" : "warn"}>
                      {m.pricing.verified ? "verified" : "unverified"}
                    </Badge>
                    {m.pricing.verified_at && (
                      <span className="mono text-[10px] text-[var(--text-faint)]">
                        {m.pricing.verified_at}
                      </span>
                    )}
                  </div>
                </div>
              </div>
              {m.pricing.staleness_warning && (
                <p className="mt-2 text-xs text-[var(--warn)]">{m.pricing.staleness_warning}</p>
              )}
              <div className="mt-3 flex flex-wrap gap-1.5">
                {m.capabilities.map((c) => (
                  <Badge key={c}>{c}</Badge>
                ))}
              </div>
              <p className="mt-2 text-xs text-[var(--text-faint)]">
                context {m.context_window.toLocaleString()} tokens · quality {m.quality_index}/100 · speed{" "}
                {m.speed_index}/100
                {m.pricing.source && (
                  <>
                    {" · "}
                    <a
                      href={m.pricing.source.startsWith("http") ? m.pricing.source : undefined}
                      className="underline"
                      target="_blank"
                      rel="noreferrer"
                    >
                      source
                    </a>
                  </>
                )}
              </p>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
