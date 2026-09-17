import Link from "next/link";

export default function Home() {
  return (
    <div className="flex flex-1 flex-col" style={{ background: "var(--bg)" }}>
      <header className="flex items-center justify-between px-6 py-5 sm:px-10">
        <div className="flex items-center gap-2">
          <div
            className="flex h-7 w-7 items-center justify-center rounded-md text-sm font-bold text-white"
            style={{ background: "var(--accent)" }}
          >
            T
          </div>
          <span className="text-sm font-semibold tracking-tight">Throughline</span>
        </div>
        <nav className="flex items-center gap-2">
          <Link
            href="/login"
            className="rounded-[var(--radius-md)] px-3.5 py-2 text-sm font-medium text-[var(--text-muted)] hover:text-[var(--text)]"
          >
            Sign in
          </Link>
          <Link
            href="/signup"
            className="rounded-[var(--radius-md)] px-3.5 py-2 text-sm font-medium text-white"
            style={{ background: "var(--accent)" }}
          >
            Get started
          </Link>
        </nav>
      </header>

      <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col items-start justify-center px-6 py-16 sm:px-10">
        <span
          className="mono mb-5 rounded-full border px-2.5 py-1 text-[11px]"
          style={{ borderColor: "var(--border)", color: "var(--text-muted)" }}
        >
          intent → optimised request → routed model → evaluated result
        </span>
        <h1 className="text-4xl font-semibold leading-[1.1] tracking-tight sm:text-5xl">
          The system decides
          <br />
          how hard to try.
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
            className="rounded-[var(--radius-md)] px-5 py-2.5 text-sm font-medium text-white"
            style={{ background: "var(--accent)" }}
          >
            Start optimising requests
          </Link>
          <Link
            href="/login"
            className="rounded-[var(--radius-md)] border px-5 py-2.5 text-sm font-medium"
            style={{ borderColor: "var(--border-strong)" }}
          >
            Sign in
          </Link>
        </div>

        <dl className="mt-16 grid w-full grid-cols-1 gap-6 border-t pt-10 sm:grid-cols-3" style={{ borderColor: "var(--border)" }}>
          <div>
            <dt className="mono text-xs text-[var(--text-faint)]">principle</dt>
            <dd className="mt-1 text-sm text-[var(--text)]">
              Longer prompt ≠ better prompt. Simple requests pass through untouched.
            </dd>
          </div>
          <div>
            <dt className="mono text-xs text-[var(--text-faint)]">honesty</dt>
            <dd className="mt-1 text-sm text-[var(--text)]">
              Never &ldquo;100% accurate.&rdquo; Every score ships with its source and confidence.
            </dd>
          </div>
          <div>
            <dt className="mono text-xs text-[var(--text-faint)]">transparency</dt>
            <dd className="mt-1 text-sm text-[var(--text)]">
              See why a prompt was changed, which model was picked, and what it cost — before you run it.
            </dd>
          </div>
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
