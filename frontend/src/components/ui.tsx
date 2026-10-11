"use client";

// Small shared primitives. Kept in one file deliberately at this app's size — splitting into a
// component-per-file library would be premature structure for ~10 components.

import type { ReactNode } from "react";

export function Card({
  children,
  className = "",
  interactive = false,
}: {
  children: ReactNode;
  className?: string;
  /** Row/tile that is actually clickable — gets an edge-engage hover rather than sitting static. */
  interactive?: boolean;
}) {
  return (
    <div
      className={`rounded-[var(--radius-lg)] border bg-[var(--surface)] ${interactive ? "lift-on-hover cursor-pointer overflow-hidden" : ""} ${className}`}
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
  // A button is a switch, not a balloon: it sinks in on press and releases, it doesn't float up on
  // hover. No shadow, no lift — just a state change (fill/border) and a real, small displacement on click.
  const base =
    "inline-flex items-center justify-center gap-1.5 font-medium rounded-[var(--radius-md)] " +
    "transition-[background-color,border-color,transform] duration-[var(--dur-fast)] ease-[var(--ease-press)] " +
    "active:translate-y-px disabled:opacity-40 disabled:cursor-not-allowed disabled:active:translate-y-0";
  const sizes = size === "sm" ? "text-xs px-2.5 py-1.5" : "text-sm px-3.5 py-2";
  const variants: Record<string, string> = {
    primary: "bg-[var(--accent)] text-white hover:bg-[var(--accent-strong)]",
    secondary: "bg-[var(--surface)] text-[var(--text)] border hover:bg-[var(--accent-tint)] hover:border-[var(--accent)]",
    ghost: "text-[var(--text-muted)] hover:text-[var(--text)] hover:bg-[var(--accent-tint)]",
    danger: "bg-[var(--danger)] text-white hover:brightness-110",
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
  // A labelled reading, not a status pill: square corners, a left tick instead of a fully-rounded chip —
  // the shape every AI dashboard uses for exactly this. Reads like a gauge annotation instead.
  const tones: Record<string, string> = {
    neutral: "text-[var(--text-muted)] border-[var(--border-strong)]",
    ok: "text-[var(--ok)] border-[var(--ok)]",
    warn: "text-[var(--warn)] border-[var(--warn)]",
    danger: "text-[var(--danger)] border-[var(--danger)]",
    accent: "text-[var(--accent-strong)] border-[var(--accent)]",
  };
  return (
    <span
      className={`mono inline-flex items-center border-l-2 pl-1.5 pr-0.5 py-0.5 text-[11px] font-medium leading-none ${tones[tone]} ${className}`}
    >
      {children}
    </span>
  );
}

export function ConfidenceBadge({ level }: { level: string }) {
  const tone = level === "high" ? "ok" : level === "moderate" ? "warn" : "danger";
  return <Badge tone={tone}>{level} confidence</Badge>;
}

const FIELD_BASE =
  "w-full rounded-[var(--radius-md)] border bg-[var(--surface)] text-[var(--text)] placeholder:text-[var(--text-faint)] " +
  "transition-[border-color,box-shadow] duration-[var(--dur-fast)] ease-[var(--ease-soft)] " +
  "hover:border-[var(--text-faint)] focus:border-[var(--accent)] focus:shadow-[0_0_0_3px_rgba(var(--accent-rgb),0.14)] focus:outline-none";

export function TextArea({
  className = "",
  ...props
}: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      className={`${FIELD_BASE} resize-none px-3.5 py-3 text-sm ${className}`}
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
      className={`${FIELD_BASE} px-3 py-2 text-sm ${className}`}
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
  // Three breathing dots rather than the ubiquitous spinning-ring glyph every loading state in every
  // AI product uses — same footprint and "still working" meaning, distinct silhouette.
  return (
    <span className={`inline-flex items-center gap-[3px] ${className}`} role="status" aria-label="Loading">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="dot-pulse h-[5px] w-[5px] rounded-full bg-current"
          style={{ animationDelay: `${i * 0.16}s` }}
        />
      ))}
    </span>
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
    <div
      className="animate-fade-in flex flex-col items-center justify-center gap-2 rounded-[var(--radius-lg)] border border-dashed py-16 text-center"
      style={{ borderColor: "var(--border-strong)" }}
    >
      <svg width="28" height="28" viewBox="0 0 28 28" fill="none" className="mb-1 text-[var(--text-faint)]" aria-hidden="true">
        <path
          d="M14 4v6m0 8v6M4 14h6m8 0h6"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          opacity="0.5"
        />
        <circle cx="14" cy="14" r="4.5" stroke="currentColor" strokeWidth="1.6" />
      </svg>
      <p className="font-[family-name:var(--font-display)] text-base font-medium text-[var(--text)]">{title}</p>
      {description && <p className="max-w-sm text-sm text-[var(--text-muted)]">{description}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function ErrorBanner({ message }: { message: string }) {
  return (
    <div
      role="alert"
      className="animate-fade-in rounded-[var(--radius-md)] border px-3.5 py-2.5 text-sm"
      style={{ borderColor: "var(--danger)", background: "var(--danger-tint)", color: "var(--danger)" }}
    >
      {message}
    </div>
  );
}

export function WarningBanner({ message }: { message: string }) {
  return (
    <div
      className="animate-fade-in rounded-[var(--radius-md)] border px-3.5 py-2.5 text-sm"
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
