"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import * as api from "@/lib/api";
import { ApiError } from "@/lib/api";
import type {
  AuditLogEntry,
  MemberResponse,
  OrgRole,
  OrgSettingsResponse,
  ProviderAccountResponse,
} from "@/lib/types";
import {
  Badge,
  Button,
  Card,
  ErrorBanner,
  Input,
  Label,
  Spinner,
  formatUsd,
} from "@/components/ui";

const ROLES: OrgRole[] = ["owner", "admin", "member", "viewer"];

export default function AdminPage() {
  const { user, loading } = useAuth();
  const router = useRouter();
  const isAdmin = user && (user.role === "owner" || user.role === "admin");

  useEffect(() => {
    if (!loading && user && !isAdmin) router.replace("/playground");
  }, [loading, user, isAdmin, router]);

  if (loading || !user) {
    return (
      <div className="flex flex-1 items-center justify-center py-20">
        <Spinner className="text-[var(--text-faint)]" />
      </div>
    );
  }
  if (!isAdmin) return null;

  return (
    <div className="mx-auto max-w-4xl px-6 py-8 sm:px-10">
      <h1 className="text-2xl font-semibold tracking-tight">Admin</h1>
      <p className="mt-1 text-sm text-[var(--text-muted)]">
        Workspace administration for {user.org_name}. Changes here affect everyone in this organization.
      </p>

      <div className="mt-6 space-y-6">
        <OrganizationSettingsSection />
        <MembersSection currentUserId={user.id} />
        <ProviderAccountsSection />
        <AuditLogSection />
      </div>
    </div>
  );
}

// ======================================================================================
// Organization settings
// ======================================================================================
function OrganizationSettingsSection() {
  const [org, setOrg] = useState<OrgSettingsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [name, setName] = useState("");
  const [budget, setBudget] = useState("");
  const [retention, setRetention] = useState("");
  const [storeRaw, setStoreRaw] = useState(false);

  function load() {
    api
      .getOrganizationSettings()
      .then((o) => {
        setOrg(o);
        setName(o.name);
        setBudget(String(o.monthly_budget_usd));
        setRetention(String(o.retention_days));
        setStoreRaw(o.store_raw_prompts);
      })
      .catch(() => setError("Could not load organization settings."));
  }

  useEffect(load, []);

  async function onSave() {
    setError(null);
    setSaved(false);
    setSaving(true);
    try {
      const updated = await api.updateOrganizationSettings({
        name,
        monthly_budget_usd: Number(budget),
        retention_days: Number(retention),
        store_raw_prompts: storeRaw,
      });
      setOrg(updated);
      setSaved(true);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not save organization settings.");
    } finally {
      setSaving(false);
    }
  }

  if (!org) {
    return (
      <Card className="p-4">
        <p className="text-sm font-medium text-[var(--text)]">Organization settings</p>
        <div className="mt-4 flex justify-center">
          <Spinner className="text-[var(--text-faint)]" />
        </div>
      </Card>
    );
  }

  return (
    <Card className="p-4">
      <div className="flex items-center justify-between">
        <p className="text-sm font-medium text-[var(--text)]">Organization settings</p>
        <Badge>{org.plan}</Badge>
      </div>
      {error && (
        <div className="mt-3">
          <ErrorBanner message={error} />
        </div>
      )}

      <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <Label>Name</Label>
          <Input value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div>
          <Label>Monthly budget (USD)</Label>
          <Input
            type="number"
            min="0"
            step="1"
            value={budget}
            onChange={(e) => setBudget(e.target.value)}
          />
          <p className="mt-1 text-xs text-[var(--text-faint)]">
            Current spend: {formatUsd(org.current_spend_usd)}
          </p>
        </div>
        <div>
          <Label>Retention (days)</Label>
          <Input
            type="number"
            min="1"
            max="3650"
            value={retention}
            onChange={(e) => setRetention(e.target.value)}
          />
        </div>
        <div className="flex items-end">
          <label className="flex items-center gap-2 text-sm text-[var(--text)]">
            <input
              type="checkbox"
              checked={storeRaw}
              onChange={(e) => setStoreRaw(e.target.checked)}
            />
            Store raw prompt content
          </label>
        </div>
      </div>
      <p className="mt-2 text-xs text-[var(--text-faint)]">
        Disabling raw-prompt storage affects privacy, not functionality — executions still run and are
        counted, but prompt/output text is not persisted beyond what is needed to serve the response.
      </p>

      <div className="mt-4 flex items-center gap-3">
        <Button onClick={onSave} disabled={saving}>
          {saving ? <Spinner className="text-white" /> : "Save changes"}
        </Button>
        {saved && <span className="text-xs text-[var(--ok)]">Saved.</span>}
      </div>
    </Card>
  );
}

// ======================================================================================
// Members
// ======================================================================================
function MembersSection({ currentUserId }: { currentUserId: string }) {
  const [members, setMembers] = useState<MemberResponse[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<OrgRole>("member");
  const [adding, setAdding] = useState(false);

  function load() {
    api.listMembers().then(setMembers).catch(() => setError("Could not load members."));
  }

  useEffect(load, []);

  async function onAdd() {
    if (!email.trim()) return;
    setError(null);
    setAdding(true);
    try {
      await api.addMember(email.trim(), role);
      setEmail("");
      load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not add member.");
    } finally {
      setAdding(false);
    }
  }

  async function onRoleChange(userId: string, newRole: OrgRole) {
    setError(null);
    try {
      await api.updateMemberRole(userId, newRole);
      load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not change role.");
    }
  }

  async function onRemove(userId: string) {
    setError(null);
    try {
      await api.removeMember(userId);
      load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not remove member.");
    }
  }

  return (
    <Card className="p-4">
      <p className="text-sm font-medium text-[var(--text)]">Members</p>
      {error && (
        <div className="mt-3">
          <ErrorBanner message={error} />
        </div>
      )}

      <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-end">
        <div className="flex-1">
          <Label>Add by email</Label>
          <Input
            type="email"
            placeholder="teammate@company.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        <div>
          <Label>Role</Label>
          <select
            value={role}
            onChange={(e) => setRole(e.target.value as OrgRole)}
            className="rounded-[var(--radius-md)] border bg-[var(--surface)] px-3 py-2 text-sm text-[var(--text)]"
            style={{ borderColor: "var(--border-strong)" }}
          >
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </div>
        <Button onClick={onAdd} disabled={adding || !email.trim()}>
          {adding ? <Spinner className="text-white" /> : "Add member"}
        </Button>
      </div>
      <p className="mt-1.5 text-xs text-[var(--text-faint)]">
        There is no email-invitation flow yet — the person must already have a Throughline account.
      </p>

      {!members && (
        <div className="mt-4 flex justify-center">
          <Spinner className="text-[var(--text-faint)]" />
        </div>
      )}

      {members && members.length > 0 && (
        <div className="mt-4 divide-y" style={{ borderColor: "var(--border)" }}>
          {members.map((m) => (
            <div key={m.user_id} className="flex items-center justify-between gap-3 py-2.5">
              <div className="min-w-0">
                <p className="truncate text-sm text-[var(--text)]">{m.display_name || m.email}</p>
                <p className="truncate text-xs text-[var(--text-faint)]">{m.email}</p>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <select
                  value={m.role}
                  onChange={(e) => onRoleChange(m.user_id, e.target.value as OrgRole)}
                  className="rounded-[var(--radius-md)] border bg-[var(--surface)] px-2 py-1.5 text-xs text-[var(--text)]"
                  style={{ borderColor: "var(--border-strong)" }}
                >
                  {ROLES.map((r) => (
                    <option key={r} value={r}>
                      {r}
                    </option>
                  ))}
                </select>
                <Button
                  variant="danger"
                  size="sm"
                  onClick={() => onRemove(m.user_id)}
                  disabled={m.user_id === currentUserId}
                  title={m.user_id === currentUserId ? "You cannot remove yourself here." : "Remove"}
                >
                  Remove
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

// ======================================================================================
// Provider accounts
// ======================================================================================
function ProviderAccountsSection() {
  const [accounts, setAccounts] = useState<ProviderAccountResponse[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [provider, setProvider] = useState("groq");
  const [label, setLabel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [adding, setAdding] = useState(false);

  function load() {
    api
      .listProviderAccounts()
      .then(setAccounts)
      .catch(() => setError("Could not load provider accounts."));
  }

  useEffect(load, []);

  async function onAdd() {
    if (!apiKey.trim()) return;
    setError(null);
    setAdding(true);
    try {
      await api.createProviderAccount({ provider, label, api_key: apiKey.trim() });
      setApiKey("");
      setLabel("");
      load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not save provider account.");
    } finally {
      setAdding(false);
    }
  }

  async function onDelete(id: string) {
    setError(null);
    try {
      await api.deleteProviderAccount(id);
      load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not delete provider account.");
    }
  }

  return (
    <Card className="p-4">
      <p className="text-sm font-medium text-[var(--text)]">Provider accounts (bring your own key)</p>
      <p className="mt-1 text-xs text-[var(--text-faint)]">
        Keys are encrypted at rest and never shown again after creation. Recommended free option:{" "}
        <code className="mono">groq</code>.
      </p>
      {error && (
        <div className="mt-3">
          <ErrorBanner message={error} />
        </div>
      )}

      <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-[140px_1fr_1fr_auto] sm:items-end">
        <div>
          <Label>Provider</Label>
          <select
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
            className="w-full rounded-[var(--radius-md)] border bg-[var(--surface)] px-3 py-2 text-sm text-[var(--text)]"
            style={{ borderColor: "var(--border-strong)" }}
          >
            <option value="groq">groq</option>
            <option value="anthropic">anthropic</option>
            <option value="openai">openai</option>
            <option value="openrouter">openrouter</option>
          </select>
        </div>
        <div>
          <Label>Label (optional)</Label>
          <Input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="production key" />
        </div>
        <div>
          <Label>API key</Label>
          <Input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder="sk-..."
          />
        </div>
        <Button onClick={onAdd} disabled={adding || !apiKey.trim()}>
          {adding ? <Spinner className="text-white" /> : "Save key"}
        </Button>
      </div>

      {!accounts && (
        <div className="mt-4 flex justify-center">
          <Spinner className="text-[var(--text-faint)]" />
        </div>
      )}

      {accounts && accounts.length > 0 && (
        <div className="mt-4 divide-y" style={{ borderColor: "var(--border)" }}>
          {accounts.map((a) => (
            <div key={a.id} className="flex items-center justify-between gap-3 py-2.5">
              <div className="min-w-0">
                <p className="text-sm text-[var(--text)]">
                  {a.provider} {a.label && `· ${a.label}`}
                </p>
                <p className="mono text-xs text-[var(--text-faint)]">ends in {a.key_last_four}</p>
              </div>
              <Button variant="danger" size="sm" onClick={() => onDelete(a.id)}>
                Delete
              </Button>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

// ======================================================================================
// Audit log
// ======================================================================================
function AuditLogSection() {
  const [entries, setEntries] = useState<AuditLogEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listAuditLogs({ limit: 50 })
      .then(setEntries)
      .catch(() => setError("Could not load the audit log."));
  }, []);

  return (
    <Card className="p-4">
      <p className="text-sm font-medium text-[var(--text)]">Audit log</p>
      {error && (
        <div className="mt-3">
          <ErrorBanner message={error} />
        </div>
      )}

      {!entries && (
        <div className="mt-4 flex justify-center">
          <Spinner className="text-[var(--text-faint)]" />
        </div>
      )}

      {entries && entries.length === 0 && (
        <p className="mt-3 text-sm text-[var(--text-muted)]">No activity recorded yet.</p>
      )}

      {entries && entries.length > 0 && (
        <div className="mt-3 max-h-96 space-y-2 overflow-y-auto">
          {entries.map((e) => (
            <div
              key={e.id}
              className="flex items-start justify-between gap-3 border-b pb-2 text-xs"
              style={{ borderColor: "var(--border)" }}
            >
              <div className="min-w-0">
                <p className="mono text-[var(--text)]">{e.action}</p>
                <p className="truncate text-[var(--text-faint)]">
                  {e.resource_type} {e.resource_id}
                </p>
              </div>
              <p className="shrink-0 text-[var(--text-faint)]">
                {new Date(e.occurred_at).toLocaleString()}
              </p>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
