"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import * as api from "@/lib/api";
import { ApiError } from "@/lib/api";
import type { ApiKeyCreatedResponse, ApiKeySummary, DataExportResponse } from "@/lib/types";
import { Badge, Button, Card, ErrorBanner, Input, Label, Spinner } from "@/components/ui";

export default function SettingsPage() {
  const { user, signOut, refresh } = useAuth();
  if (!user) return null;

  return (
    <div className="mx-auto max-w-2xl px-6 py-8 sm:px-10">
      <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>

      <ProfileSection
        displayName={user.display_name}
        email={user.email}
        orgName={user.org_name}
        role={user.role}
        memoryEnabled={user.memory_enabled}
        onUpdated={refresh}
      />

      <ChangePasswordSection />

      <ApiKeysSection />

      <DataExportSection />

      <DangerZoneSection onDeleted={signOut} />
    </div>
  );
}

// ======================================================================================
// Profile
// ======================================================================================
function ProfileSection({
  displayName,
  email,
  orgName,
  role,
  memoryEnabled,
  onUpdated,
}: {
  displayName: string;
  email: string;
  orgName: string;
  role: string;
  memoryEnabled: boolean;
  onUpdated: () => void;
}) {
  const [name, setName] = useState(displayName);
  const [memory, setMemory] = useState(memoryEnabled);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const dirty = name !== displayName || memory !== memoryEnabled;

  async function onSave() {
    setError(null);
    setSaved(false);
    setSaving(true);
    try {
      await api.updateProfile({ display_name: name, memory_enabled: memory });
      onUpdated();
      setSaved(true);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not update profile.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card className="mt-6 p-4">
      <p className="text-sm font-medium text-[var(--text)]">Account</p>
      {error && (
        <div className="mt-3">
          <ErrorBanner message={error} />
        </div>
      )}
      <div className="mt-3 space-y-3 text-sm">
        <div>
          <Label>Name</Label>
          <Input value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <Row label="Email" value={email} />
        <Row label="Workspace" value={orgName} />
        <Row label="Role" value={<Badge>{role}</Badge>} />
        <label className="flex items-center gap-2 pt-1">
          <input type="checkbox" checked={memory} onChange={(e) => setMemory(e.target.checked)} />
          <span>Remember my preferences across sessions</span>
        </label>
      </div>
      <div className="mt-4 flex items-center gap-3">
        <Button onClick={onSave} disabled={saving || !dirty} size="sm">
          {saving ? <Spinner className="text-white" /> : "Save changes"}
        </Button>
        {saved && !dirty && <span className="text-xs text-[var(--ok)]">Saved.</span>}
      </div>
    </Card>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between border-b pb-2" style={{ borderColor: "var(--border)" }}>
      <dt className="text-[var(--text-muted)]">{label}</dt>
      <dd className="text-[var(--text)]">{value}</dd>
    </div>
  );
}

// ======================================================================================
// Change password
// ======================================================================================
function ChangePasswordSection() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSave() {
    setError(null);
    setSaved(false);
    if (next !== confirm) {
      setError("New password and confirmation do not match.");
      return;
    }
    setSaving(true);
    try {
      await api.changePassword(current, next);
      setCurrent("");
      setNext("");
      setConfirm("");
      setSaved(true);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not change password.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card className="mt-4 p-4">
      <p className="text-sm font-medium text-[var(--text)]">Change password</p>
      {error && (
        <div className="mt-3">
          <ErrorBanner message={error} />
        </div>
      )}
      <div className="mt-3 space-y-3">
        <div>
          <Label>Current password</Label>
          <Input type="password" value={current} onChange={(e) => setCurrent(e.target.value)} />
        </div>
        <div>
          <Label>New password</Label>
          <Input type="password" value={next} onChange={(e) => setNext(e.target.value)} />
        </div>
        <div>
          <Label>Confirm new password</Label>
          <Input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
        </div>
      </div>
      <div className="mt-4 flex items-center gap-3">
        <Button onClick={onSave} disabled={saving || !current || !next} size="sm">
          {saving ? <Spinner className="text-white" /> : "Update password"}
        </Button>
        {saved && <span className="text-xs text-[var(--ok)]">Password updated.</span>}
      </div>
    </Card>
  );
}

// ======================================================================================
// API keys
// ======================================================================================
function ApiKeysSection() {
  const [keys, setKeys] = useState<ApiKeySummary[] | null>(null);
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);
  const [justCreated, setJustCreated] = useState<ApiKeyCreatedResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  function load() {
    api.listApiKeys().then(setKeys).catch(() => setError("Could not load API keys."));
  }

  useEffect(load, []);

  async function onCreate() {
    if (!name.trim()) return;
    setError(null);
    setCreating(true);
    try {
      const created = await api.createApiKey(name.trim());
      setJustCreated(created);
      setName("");
      load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not create API key.");
    } finally {
      setCreating(false);
    }
  }

  async function onRevoke(id: string) {
    setError(null);
    try {
      await api.revokeApiKey(id);
      load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not revoke API key.");
    }
  }

  return (
    <Card className="mt-4 p-4">
      <p className="text-sm font-medium text-[var(--text)]">API access</p>
      <p className="mt-1 text-sm text-[var(--text-muted)]">
        Programmatic access uses an API key sent as <code className="mono">X-API-Key</code>. Admin role
        required to issue or revoke keys.
      </p>
      {error && (
        <div className="mt-3">
          <ErrorBanner message={error} />
        </div>
      )}

      {justCreated && (
        <div
          className="mt-3 rounded-[var(--radius-md)] border p-3"
          style={{ borderColor: "var(--warn)", background: "var(--warn-tint)" }}
        >
          <p className="text-xs font-medium text-[var(--warn)]">{justCreated.warning}</p>
          <p className="mono mt-2 break-all text-sm text-[var(--text)]">{justCreated.key}</p>
          <Button size="sm" variant="secondary" className="mt-2" onClick={() => setJustCreated(null)}>
            I&apos;ve saved it
          </Button>
        </div>
      )}

      <div className="mt-3 flex items-end gap-2">
        <div className="flex-1">
          <Label>New key name</Label>
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. production backend"
          />
        </div>
        <Button onClick={onCreate} disabled={creating || !name.trim()} size="sm">
          {creating ? <Spinner className="text-white" /> : "Create key"}
        </Button>
      </div>

      {!keys && (
        <div className="mt-4 flex justify-center">
          <Spinner className="text-[var(--text-faint)]" />
        </div>
      )}

      {keys && keys.length === 0 && (
        <p className="mt-4 text-sm text-[var(--text-faint)]">No API keys yet.</p>
      )}

      {keys && keys.length > 0 && (
        <div className="mt-4 divide-y" style={{ borderColor: "var(--border)" }}>
          {keys.map((k) => (
            <div key={k.id} className="flex items-center justify-between gap-3 py-2.5">
              <div className="min-w-0">
                <p className="text-sm text-[var(--text)]">
                  {k.name} {!k.is_active && <Badge tone="neutral">revoked</Badge>}
                </p>
                <p className="mono text-xs text-[var(--text-faint)]">
                  {k.key_prefix}… · created {new Date(k.created_at).toLocaleDateString()}
                  {k.last_used_at && ` · last used ${new Date(k.last_used_at).toLocaleDateString()}`}
                </p>
              </div>
              {k.is_active && (
                <Button variant="danger" size="sm" onClick={() => onRevoke(k.id)}>
                  Revoke
                </Button>
              )}
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

// ======================================================================================
// Data export
// ======================================================================================
function DataExportSection() {
  const [data, setData] = useState<DataExportResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onExport() {
    setError(null);
    setLoading(true);
    try {
      const result = await api.exportAccountData();
      setData(result);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not export data.");
    } finally {
      setLoading(false);
    }
  }

  function onDownload() {
    if (!data) return;
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "throughline-account-export.json";
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <Card className="mt-4 p-4">
      <p className="text-sm font-medium text-[var(--text)]">Your data</p>
      <p className="mt-1 text-sm text-[var(--text-muted)]">
        Export your own account record, memberships and execution count as JSON.
      </p>
      {error && (
        <div className="mt-3">
          <ErrorBanner message={error} />
        </div>
      )}
      <div className="mt-3 flex items-center gap-3">
        <Button variant="secondary" size="sm" onClick={onExport} disabled={loading}>
          {loading ? <Spinner /> : "Prepare export"}
        </Button>
        {data && (
          <Button size="sm" onClick={onDownload}>
            Download JSON
          </Button>
        )}
      </div>
    </Card>
  );
}

// ======================================================================================
// Danger zone
// ======================================================================================
function DangerZoneSection({ onDeleted }: { onDeleted: () => void }) {
  const router = useRouter();
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onDelete() {
    setError(null);
    setDeleting(true);
    try {
      await api.deleteAccount();
      onDeleted();
      router.push("/login");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not delete account.");
      setDeleting(false);
    }
  }

  return (
    <Card className="mt-4 p-4">
      <p className="text-sm font-medium text-[var(--danger)]">Danger zone</p>
      <p className="mt-1 text-sm text-[var(--text-muted)]">
        Permanently delete your account. If you are the sole owner of a workspace with other members,
        transfer ownership or remove them first.
      </p>
      {error && (
        <div className="mt-3">
          <ErrorBanner message={error} />
        </div>
      )}
      <div className="mt-3">
        {!confirming ? (
          <Button variant="danger" size="sm" onClick={() => setConfirming(true)}>
            Delete my account
          </Button>
        ) : (
          <div className="flex items-center gap-3">
            <Button variant="danger" size="sm" onClick={onDelete} disabled={deleting}>
              {deleting ? <Spinner className="text-white" /> : "Yes, permanently delete"}
            </Button>
            <Button variant="secondary" size="sm" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
          </div>
        )}
      </div>
    </Card>
  );
}
