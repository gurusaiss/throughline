import Link from "next/link";
import { Logomark } from "@/components/logomark";

const PRINCIPLES = [
  {
    tag: "principle",
    body: "Longer prompt ≠ better prompt. Simple requests pass through untouched.",
  },
  {
    tag: "honesty",
    body: "Never “100% accurate.” Every score ships with its source and confidence.",
  },
  {
    tag: "transparency",
    body: "See why a prompt was changed, which model was picked, and what it cost — before you run it.",
  },
];

export default function Home() {
  return (
    <div className="flex flex-1 flex-col" style={{ background: "var(--bg)" }}>
      <header className="flex items-center justify-between px-6 py-5 sm:px-10">
        <div className="group flex items-center gap-2">
          <span className="text-[var(--text)] transition-transform duration-[var(--dur-base)] ease-[var(--ease-soft)] group-hover:rotate-180">
            <Logomark size={22} />
          </span>
          <span className="font-[family-name:var(--font-display)] text-base font-semibold tracking-tight">
            Throughline
          </span>
        </div>
        <nav className="flex items-center gap-1">
          <Link
            href="/login"
            className="link-underline rounded-[var(--radius-md)] px-3.5 py-2 text-sm font-medium text-[var(--text-muted)] hover:text-[var(--text)]"
          >
            Sign in
          </Link>
          <Link
            href="/signup"
            className="rounded-[var(--radius-md)] px-3.5 py-2 text-sm font-medium text-white shadow-[var(--shadow-sm)] transition-[background-color,box-shadow,transform] duration-[var(--dur-fast)] ease-[var(--ease-soft)] hover:-translate-y-px hover:shadow-[var(--shadow-md)]"
            style={{ background: "var(--accent)" }}
          >
            Get started
          </Link>
        </nav>
      </header>

      <main className="mx-auto flex w-full max-w-5xl flex-1 flex-col px-6 py-16 sm:px-10">
        <div className="grid flex-1 grid-cols-1 items-center gap-12 lg:grid-cols-[1.1fr_0.9fr]">
          <div className="animate-fade-in flex flex-col items-start">
            <span
              className="mono mb-5 rounded-full border px-2.5 py-1 text-[11px]"
              style={{ borderColor: "var(--border)", color: "var(--text-muted)" }}
            >
              intent → optimised request → routed model → evaluated result
            </span>
            <h1 className="text-4xl leading-[1.08] tracking-tight sm:text-5xl">
              The system decides
              <br />
              <em>how hard to try.</em>
            </h1>
            <p className="mt-5 max-w-xl text-lg leading-relaxed text-[var(--text-muted)]">
              Throughline turns a plain-language request into an optimised prompt, routes it to the model
              that actually fits the job, and evaluates what comes back — spending exactly as much
              orchestration as the request deserves. A trivial question costs one model call. A hard one
              gets compared candidates and an evaluation you can inspect.
            </p>

            <div className="mt-8 flex flex-wrap gap-3">
              <Link
                href="/signup"
                className="rounded-[var(--radius-md)] px-5 py-2.5 text-sm font-medium text-white shadow-[var(--shadow-sm)] transition-[background-color,box-shadow,transform] duration-[var(--dur-fast)] ease-[var(--ease-soft)] hover:-translate-y-px hover:shadow-[var(--shadow-md)] active:translate-y-0 active:scale-[0.98]"
                style={{ background: "var(--accent)" }}
              >
                Start optimising requests
              </Link>
              <Link
                href="/login"
                className="rounded-[var(--radius-md)] border px-5 py-2.5 text-sm font-medium transition-colors duration-[var(--dur-fast)] hover:bg-[var(--accent-tint)] hover:border-[var(--accent)]"
                style={{ borderColor: "var(--border-strong)" }}
              >
                Sign in
              </Link>
            </div>
          </div>

          <SignalDiagram />
        </div>

        <dl className="mt-16 grid w-full grid-cols-1 gap-3 border-t pt-10 sm:grid-cols-3" style={{ borderColor: "var(--border)" }}>
          {PRINCIPLES.map((p, i) => (
            <div
              key={p.tag}
              className="lift-on-hover stagger-rise-in rounded-[var(--radius-md)] border border-transparent p-3 -m-3"
              style={{ "--stagger-index": i } as React.CSSProperties}
            >
              <dt className="mono text-xs text-[var(--text-faint)]">{p.tag}</dt>
              <dd className="mt-1 text-sm text-[var(--text)]">{p.body}</dd>
            </div>
          ))}
        </dl>
      </main>

      <footer
        className="px-6 py-6 text-xs text-[var(--text-faint)] sm:px-10"
        style={{ borderTop: "1px solid var(--border)" }}
      >
        Throughline — an adaptive AI execution layer.
      </footer>
    </div>
  );
}

// The hero's one illustration: a signal travelling through three stages, each annotated with what
// actually happens there. Drawn, not stocked — reinforces the "instrument panel" identity instead of a
// generic abstract gradient blob.
function SignalDiagram() {
  const stages = [
    { label: "intent", detail: "signals extracted, risk scored" },
    { label: "optimised", detail: "compiled, routed to a model" },
    { label: "evaluated", detail: "checked before it reaches you" },
  ];
  return (
    <div className="animate-fade-in hidden rounded-[var(--radius-lg)] border bg-[var(--surface)] p-6 shadow-[var(--shadow-sm)] lg:block" style={{ borderColor: "var(--border)" }}>
      <svg viewBox="0 0 280 160" className="w-full" aria-hidden="true">
        <line x1="20" y1="80" x2="260" y2="80" stroke="var(--border-strong)" strokeWidth="1.5" />
        {[20, 140, 260].map((x, i) => (
          <circle key={x} cx={x} cy="80" r={i === 1 ? 7 : 5} fill={i === 1 ? "var(--accent)" : "var(--border-strong)"} />
        ))}
        <circle cx="140" cy="80" r="14" fill="none" stroke="var(--accent)" strokeWidth="1" opacity="0.35">
          <animate attributeName="r" values="7;16;7" dur="2.4s" repeatCount="indefinite" />
          <animate attributeName="opacity" values="0.5;0;0.5" dur="2.4s" repeatCount="indefinite" />
        </circle>
      </svg>
      <div className="mt-2 grid grid-cols-3 gap-2 text-center">
        {stages.map((s) => (
          <div key={s.label}>
            <p className="mono text-xs font-medium text-[var(--text)]">{s.label}</p>
            <p className="mt-0.5 text-[11px] leading-snug text-[var(--text-faint)]">{s.detail}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
