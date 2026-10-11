// The product's one visual signature: a line passing through three calibration points, the middle one
// lit — intent, optimisation, evaluation. Deliberately not a letter-in-a-rounded-square, which is what
// every generated SaaS/AI landing page defaults to.
export function Logomark({ className = "", size = 22 }: { className?: string; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      className={className}
      aria-hidden="true"
    >
      <line x1="2" y1="12" x2="22" y2="12" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" opacity="0.35" />
      <circle cx="5" cy="12" r="2" fill="currentColor" opacity="0.35" />
      <circle cx="19" cy="12" r="2" fill="currentColor" opacity="0.35" />
      <circle cx="12" cy="12" r="4" fill="var(--accent)" />
    </svg>
  );
}
