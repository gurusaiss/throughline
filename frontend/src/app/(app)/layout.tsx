"use client";

import { useEffect } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { Spinner } from "@/components/ui";
import { Logomark } from "@/components/logomark";

const NAV = [
  { href: "/playground", label: "Playground", hint: "run a request" },
  { href: "/compare", label: "Compare", hint: "raw vs optimised" },
  { href: "/prompts", label: "Prompts", hint: "library & versions" },
  { href: "/models", label: "Models", hint: "registry & pricing" },
  { href: "/history", label: "History", hint: "past executions" },
  { href: "/analytics", label: "Analytics", hint: "cost & usage" },
  { href: "/settings", label: "Settings", hint: "org & account" },
];

const ADMIN_NAV_ITEM = { href: "/admin", label: "Admin", hint: "members, billing, audit" };

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { user, loading, signOut } = useAuth();
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !user) router.replace("/login");
  }, [loading, user, router]);

  if (loading || !user) {
    return (
      <div className="flex flex-1 items-center justify-center">
        <Spinner className="text-[var(--text-faint)]" />
      </div>
    );
  }

  const isAdmin = user.role === "owner" || user.role === "admin";
  const navItems = isAdmin ? [...NAV, ADMIN_NAV_ITEM] : NAV;
  const initial = (user.display_name || user.email || "?").trim().charAt(0).toUpperCase();

  return (
    <div className="flex flex-1" style={{ background: "var(--bg)" }}>
      <aside
        className="hidden w-56 shrink-0 flex-col border-r px-3 py-4 sm:flex"
        style={{ borderColor: "var(--border)" }}
      >
        <Link href="/playground" className="group mb-6 flex items-center gap-2 px-2">
          <span className="text-[var(--text)] transition-transform duration-[var(--dur-base)] ease-[var(--ease-soft)] group-hover:rotate-180">
            <Logomark size={20} />
          </span>
          <span className="font-[family-name:var(--font-display)] text-base font-semibold tracking-tight">
            Throughline
          </span>
        </Link>

        <nav className="flex flex-1 flex-col gap-0.5">
          {navItems.map((item) => {
            const active = pathname?.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                className="relative rounded-[var(--radius-md)] px-2.5 py-2 text-sm transition-[background-color,color,transform] duration-[var(--dur-fast)] ease-[var(--ease-soft)] hover:translate-x-0.5"
                style={{
                  background: active ? "var(--accent-tint)" : "transparent",
                  color: active ? "var(--accent-strong)" : "var(--text-muted)",
                  fontWeight: active ? 600 : 500,
                }}
              >
                {active && (
                  <span
                    className="absolute left-0 top-1/2 h-4 w-[3px] -translate-y-1/2 rounded-full"
                    style={{ background: "var(--accent)" }}
                    aria-hidden="true"
                  />
                )}
                {item.label}
              </Link>
            );
          })}
        </nav>

        <div className="mt-auto border-t pt-3" style={{ borderColor: "var(--border)" }}>
          <div className="flex items-center gap-2 px-2.5">
            <span
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-semibold text-white"
              style={{ background: "var(--accent)" }}
              aria-hidden="true"
            >
              {initial}
            </span>
            <div className="min-w-0">
              <p className="truncate text-xs font-medium text-[var(--text)]">{user.org_name}</p>
              <p className="truncate text-xs text-[var(--text-faint)]">{user.email}</p>
            </div>
          </div>
          <button
            onClick={signOut}
            className="mt-1.5 w-full rounded-[var(--radius-md)] px-2.5 py-1.5 text-left text-xs text-[var(--text-muted)] transition-colors duration-[var(--dur-fast)] hover:bg-[var(--accent-tint)] hover:text-[var(--text)]"
          >
            Sign out
          </button>
        </div>
      </aside>

      <MobileNav pathname={pathname} />

      <main className="flex-1 overflow-y-auto pb-16 sm:pb-0">{children}</main>
    </div>
  );
}

function MobileNav({ pathname }: { pathname: string | null }) {
  return (
    <nav
      className="fixed inset-x-0 bottom-0 z-20 flex justify-around border-t bg-[var(--surface)] py-1.5 sm:hidden"
      style={{ borderColor: "var(--border)" }}
      aria-label="Primary"
    >
      {NAV.slice(0, 5).map((item) => {
        const active = pathname?.startsWith(item.href);
        return (
          <Link
            key={item.href}
            href={item.href}
            className="flex flex-col items-center gap-0.5 px-2 py-1 text-[10px] transition-colors duration-[var(--dur-fast)]"
            style={{ color: active ? "var(--accent)" : "var(--text-faint)" }}
          >
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}
