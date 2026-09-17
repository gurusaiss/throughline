"use client";

import { useAuth } from "@/lib/auth-context";
import { Badge, Card } from "@/components/ui";

export default function SettingsPage() {
  const { user } = useAuth();
  if (!user) return null;

  return (
    <div className="mx-auto max-w-2xl px-6 py-8 sm:px-10">
      <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>

      <Card className="mt-6 p-4">
        <p className="text-sm font-medium text-[var(--text)]">Account</p>
        <dl className="mt-3 space-y-2 text-sm">
          <Row label="Name" value={user.display_name} />
          <Row label="Email" value={user.email} />
          <Row label="Workspace" value={user.org_name} />
          <Row label="Role" value={<Badge>{user.role}</Badge>} />
        </dl>
      </Card>

      <Card className="mt-4 p-4">
        <p className="text-sm font-medium text-[var(--text)]">Memory & privacy</p>
        <p className="mt-1 text-sm text-[var(--text-muted)]">
          Preference memory is {user.memory_enabled ? "enabled" : "disabled"}. Raw prompt content is not
          retained by default for your workspace — nothing here is retained silently. Changing the
          retention setting currently requires a direct database change by an operator; a settings
          control for this is planned (see <code className="mono">docs/ROADMAP.md</code>).
        </p>
      </Card>

      <Card className="mt-4 p-4">
        <p className="text-sm font-medium text-[var(--text)]">API access</p>
        <p className="mt-1 text-sm text-[var(--text-muted)]">
          Programmatic access uses an API key sent as <code className="mono">X-API-Key</code>. Issuing
          and revoking keys from this UI is not built yet — it is the next item in{" "}
          <code className="mono">docs/ROADMAP.md</code>. An operator can create one directly today.
        </p>
      </Card>
    </div>
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
