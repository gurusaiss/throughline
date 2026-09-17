"use client";

import { useState } from "react";
import Link from "next/link";
import { useAuth } from "@/lib/auth-context";
import { ApiError } from "@/lib/api";
import { Button, ErrorBanner, Input, Label } from "./ui";

export function AuthForm({ mode }: { mode: "login" | "signup" }) {
  const { signIn, signUp } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [orgName, setOrgName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (mode === "login") {
        await signIn(email, password);
      } else {
        await signUp(email, password, displayName || undefined, orgName || undefined);
      }
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("Something went wrong. Please try again.");
      }
      setBusy(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="w-full max-w-sm space-y-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">
          {mode === "login" ? "Sign in" : "Create your workspace"}
        </h1>
        <p className="mt-1 text-sm text-[var(--text-muted)]">
          {mode === "login"
            ? "Welcome back."
            : "Free to start. No prompt-engineering experience required."}
        </p>
      </div>

      {error && <ErrorBanner message={error} />}

      {mode === "signup" && (
        <div>
          <Label>Your name</Label>
          <Input
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder="Ada Lovelace"
            autoComplete="name"
          />
        </div>
      )}

      <div>
        <Label>Email</Label>
        <Input
          type="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@company.com"
          autoComplete="email"
        />
      </div>

      <div>
        <Label>Password</Label>
        <Input
          type="password"
          required
          minLength={10}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="At least 10 characters"
          autoComplete={mode === "login" ? "current-password" : "new-password"}
        />
      </div>

      {mode === "signup" && (
        <div>
          <Label>Workspace name (optional)</Label>
          <Input
            value={orgName}
            onChange={(e) => setOrgName(e.target.value)}
            placeholder="My team"
          />
        </div>
      )}

      <Button type="submit" disabled={busy} className="w-full">
        {busy ? "Please wait…" : mode === "login" ? "Sign in" : "Create account"}
      </Button>

      <p className="text-center text-sm text-[var(--text-muted)]">
        {mode === "login" ? (
          <>
            No account?{" "}
            <Link href="/signup" className="font-medium text-[var(--accent)]">
              Sign up
            </Link>
          </>
        ) : (
          <>
            Already have one?{" "}
            <Link href="/login" className="font-medium text-[var(--accent)]">
              Sign in
            </Link>
          </>
        )}
      </p>
    </form>
  );
}
