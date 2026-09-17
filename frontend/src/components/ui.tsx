"use client";

// Small shared primitives. Kept in one file deliberately at this app's size — splitting into a
// component-per-file library would be premature structure for ~10 components.

import type { ReactNode } from "react";

export function Card({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`rounded-[var(--radius-lg)] border bg-[var(--surface)] shadow-[var(--shadow-sm)] ${className}`}
      style={{ borderColor: "var(--border)" }}
    >
      {children}
    </div>
  );
}

export function Button({
  children,
  variant = "primary",
  size = "md",
  className = "",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md";
}) {
  const base =
    "inline-flex items-center justify-center gap-1.5 font-medium rounded-[var(--radius-md)] transition-colors disabled:opacity-50 disabled:cursor-not-allowed";
  const sizes = size === "sm" ? "text-xs px-2.5 py-1.5" : "text-sm px-3.5 py-2";
  const variants: Record<string, string> = {
    primary:
      "bg-[var(--accent)] text-white hover:bg-[var(--accent-strong)] active:bg-[var(--accent-strong)]",
    secondary:
      "bg-[var(--surface)] text-[var(--text)] border hover:bg-[var(--accent-tint)]",
    ghost: "text-[var(--text-muted)] hover:text-[var(--text)] hover:bg-[var(--accent-tint)]",
    danger: "bg-[var(--danger)] text-white hover:opacity-90",
  };
  return (
    <button
      className={`${base} ${sizes} ${variants[variant]} ${className}`}
      style={variant === "secondary" ? { borderColor: "var(--border-strong)" } : undefined}
      {...props}
    >
      {children}
    </button>
  );
}

export function Badge({
  children,
  tone = "neutral",
  className = "",
}: {
  children: ReactNode;
  tone?: "neutral" | "ok" | "warn" | "danger" | "accent";
  className?: string;
}) {
  const tones: Record<string, string> = {
    neutral: "bg-[var(--bg)] text-[var(--text-muted)] border-[var(--border)]",
    ok: "bg-[var(--ok-tint)] text-[var(--ok)] border-transparent",
    warn: "bg-[var(--warn-tint)] text-[var(--warn)] border-transparent",
    danger: "bg-[var(--danger-tint)] text-[var(--danger)] border-transparent",
    accent: "bg-[var(--accent-tint)] text-[var(--accent-strong)] border-transparent",
  };
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium mono ${tones[tone]} ${className}`}
    >
      {children}
    </span>
  );
}

export function ConfidenceBadge({ level }: { level: string }) {
  const tone = level === "high" ? "ok" : level === "moderate" ? "warn" : "danger";
  return <Badge tone={tone}>{level} confidence</Badge>;
}

export function TextArea({
  className = "",
  ...props
}: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      className={`w-full resize-none rounded-[var(--radius-md)] border bg-[var(--surface)] px-3.5 py-3 text-sm text-[var(--text)] placeholder:text-[var(--text-faint)] focus:border-[var(--accent)] ${className}`}
      style={{ borderColor: "var(--border-strong)" }}
      {...props}
    />
  );
}

export function Input({
  className = "",
  ...props
}: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={`w-full rounded-[var(--radius-md)] border bg-[var(--surface)] px-3 py-2 text-sm text-[var(--text)] placeholder:text-[var(--text-faint)] focus:border-[var(--accent)] ${className}`}
      style={{ borderColor: "var(--border-strong)" }}
      {...props}
    />
  );
}

export function Label({ children }: { children: ReactNode }) {
  return (
    <label className="mb-1.5 block text-xs font-medium uppercase tracking-wide text-[var(--text-muted)]">
      {children}
    </label>
  );
}

export function Spinner({ className = "" }: { className?: string }) {
  return (
    <svg
      className={`animate-spin ${className}`}
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" opacity="0.25" />
      <path d="M22 12a10 10 0 0 0-10-10" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-[var(--radius-lg)] border border-dashed py-16 text-center" style={{ borderColor: "var(--border-strong)" }}>
      <p className="text-sm font-medium text-[var(--text)]">{title}</p>
      {description && <p className="max-w-sm text-sm text-[var(--text-muted)]">{description}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function ErrorBanner({ message }: { message: string }) {
  return (
    <div
      role="alert"
      className="rounded-[var(--radius-md)] border px-3.5 py-2.5 text-sm"
      style={{ borderColor: "var(--danger)", background: "var(--danger-tint)", color: "var(--danger)" }}
    >
      {message}
    </div>
  );
}

export function WarningBanner({ message }: { message: string }) {
  return (
    <div
      className="rounded-[var(--radius-md)] border px-3.5 py-2.5 text-sm"
      style={{ borderColor: "var(--warn)", background: "var(--warn-tint)", color: "var(--warn)" }}
    >
      {message}
    </div>
  );
}

export function formatUsd(value: number): string {
  if (value === 0) return "$0.00";
  if (value < 0.01) return `$${value.toFixed(6)}`;
  return `$${value.toFixed(4)}`;
}

export function formatMs(value: number): string {
  if (value < 1000) return `${value}ms`;
  return `${(value / 1000).toFixed(1)}s`;
}
