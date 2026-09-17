"use client";

import { useEffect, useState } from "react";
import * as api from "@/lib/api";
import { ApiError } from "@/lib/api";
import type { PromptResponse, PromptVersionResponse } from "@/lib/types";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorBanner,
  Input,
  Label,
  Spinner,
  TextArea,
} from "@/components/ui";

export default function PromptsPage() {
  const [prompts, setPrompts] = useState<PromptResponse[] | null>(null);
  const [selected, setSelected] = useState<PromptResponse | null>(null);
  const [versions, setVersions] = useState<PromptVersionResponse[] | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function load() {
    api.listPrompts().then(setPrompts).catch(() => setError("Could not load prompts."));
  }

  useEffect(load, []);

  async function openVersions(p: PromptResponse) {
    setSelected(p);
    setVersions(null);
    try {
      const v = await api.listPromptVersions(p.id);
      setVersions(v);
    } catch {
      setError("Could not load versions for this prompt.");
    }
  }

  async function onActivate(versionId: string) {
    if (!selected) return;
    await api.activatePromptVersion(selected.id, versionId);
    openVersions(selected);
    load();
  }

  return (
    <div className="mx-auto max-w-5xl px-6 py-8 sm:px-10">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Prompt library</h1>
          <p className="mt-1 text-sm text-[var(--text-muted)]">
            Every edit creates a new version. Rolling back changes which version is active — nothing is
            overwritten or lost.
          </p>
        </div>
        <Button onClick={() => setShowCreate((v) => !v)}>{showCreate ? "Cancel" : "New prompt"}</Button>
      </div>

      {error && (
        <div className="mt-4">
          <ErrorBanner message={error} />
        </div>
      )}

      {showCreate && (
        <CreateForm
          onCreated={() => {
            setShowCreate(false);
            load();
          }}
        />
      )}

      {!prompts && (
        <div className="mt-10 flex justify-center">
          <Spinner className="text-[var(--text-faint)]" />
        </div>
      )}

      {prompts && prompts.length === 0 && (
        <div className="mt-6">
          <EmptyState
            title="No saved prompts yet"
            description="Save a prompt from the playground, or create one here, to start versioning it."
          />
        </div>
      )}

      <div className="mt-6 grid grid-cols-1 gap-6 md:grid-cols-2">
        <div className="space-y-3">
          {prompts?.map((p) => (
            <button
              key={p.id}
              onClick={() => openVersions(p)}
              className="block w-full text-left"
            >
              <Card
                className={`p-4 transition-colors ${selected?.id === p.id ? "ring-2 ring-[var(--accent)]" : ""}`}
              >
                <div className="flex items-center justify-between">
                  <p className="font-medium text-[var(--text)]">{p.name}</p>
                  <Badge>{p.task_type}</Badge>
                </div>
                {p.description && (
                  <p className="mt-1 text-sm text-[var(--text-muted)]">{p.description}</p>
                )}
                <p className="mono mt-2 text-xs text-[var(--text-faint)]">
                  {p.version_count} version{p.version_count === 1 ? "" : "s"}
                  {p.active_version && ` · active: v${p.active_version.version}`}
                </p>
              </Card>
            </button>
          ))}
        </div>

        {selected && (
          <Card className="p-4">
            <div className="flex items-center justify-between">
              <p className="font-medium text-[var(--text)]">{selected.name}</p>
              <VersionForm
                promptId={selected.id}
                onCreated={() => openVersions(selected)}
              />
            </div>
            {!versions && (
              <div className="mt-6 flex justify-center">
                <Spinner className="text-[var(--text-faint)]" />
              </div>
            )}
            <div className="mt-3 space-y-2">
              {versions?.map((v) => (
                <div
                  key={v.id}
                  className="rounded-[var(--radius-md)] border p-3"
                  style={{ borderColor: v.is_active ? "var(--accent)" : "var(--border)" }}
                >
                  <div className="flex items-center justify-between">
                    <span className="mono text-xs font-medium">v{v.version}</span>
                    <div className="flex items-center gap-2">
                      {v.is_active && <Badge tone="accent">active</Badge>}
                      {!v.is_active && (
                        <button
                          onClick={() => onActivate(v.id)}
                          className="text-xs font-medium text-[var(--accent)]"
                        >
                          Make active
                        </button>
                      )}
                    </div>
                  </div>
                  {v.notes && <p className="mt-1 text-xs text-[var(--text-muted)]">{v.notes}</p>}
                  <p className="mono mt-2 whitespace-pre-wrap text-xs text-[var(--text-faint)]">
                    {v.system_text || "(no system prompt)"}
                  </p>
                </div>
              ))}
            </div>
          </Card>
        )}
      </div>
    </div>
  );
}

function CreateForm({ onCreated }: { onCreated: () => void }) {
  const [name, setName] = useState("");
  const [systemText, setSystemText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.createPrompt({ name, system_text: systemText });
      setName("");
      setSystemText("");
      onCreated();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create prompt.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="mt-4 p-4">
      <form onSubmit={onSubmit} className="space-y-3">
        {error && <ErrorBanner message={error} />}
        <div>
          <Label>Name</Label>
          <Input required value={name} onChange={(e) => setName(e.target.value)} placeholder="Support reply" />
        </div>
        <div>
          <Label>System prompt</Label>
          <TextArea
            rows={3}
            value={systemText}
            onChange={(e) => setSystemText(e.target.value)}
            placeholder="You are a helpful support agent…"
          />
        </div>
        <Button type="submit" disabled={busy || !name.trim()}>
          {busy ? "Saving…" : "Create prompt"}
        </Button>
      </form>
    </Card>
  );
}

function VersionForm({ promptId, onCreated }: { promptId: string; onCreated: () => void }) {
  const [open, setOpen] = useState(false);
  const [systemText, setSystemText] = useState("");
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await api.createPromptVersion(promptId, { system_text: systemText });
      setSystemText("");
      setOpen(false);
      onCreated();
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <button onClick={() => setOpen(true)} className="text-xs font-medium text-[var(--accent)]">
        + New version
      </button>
    );
  }

  return (
    <form onSubmit={onSubmit} className="flex items-center gap-2">
      <Input
        value={systemText}
        onChange={(e) => setSystemText(e.target.value)}
        placeholder="New system prompt"
        className="w-48"
      />
      <Button type="submit" size="sm" disabled={busy}>
        Save
      </Button>
    </form>
  );
}
