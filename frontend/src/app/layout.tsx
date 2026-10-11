import type { Metadata } from "next";
import { Fraunces, Public_Sans, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { AuthProvider } from "@/lib/auth-context";

// Three typefaces doing three distinct jobs, deliberately not the Inter-everywhere default most AI
// products ship with: Fraunces (a soft, slightly eccentric serif) carries headline personality; Public
// Sans — a technical, government-grade grotesk — does the quiet work of UI text; JetBrains Mono marks
// anything measured (cost, tokens, model ids) as a fact, not prose. All three are variable fonts (a single
// font-weight axis), which matters for Turbopack's dev font loader, not just bundle size.
const display = Fraunces({
  variable: "--font-display",
  subsets: ["latin"],
  axes: ["SOFT", "opsz"],
  style: ["normal", "italic"],
});
const sans = Public_Sans({
  variable: "--font-sans",
  subsets: ["latin"],
});
const mono = JetBrains_Mono({ variable: "--font-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Throughline — adaptive AI execution layer",
  description:
    "Turns intent into an optimised request, spends only as much orchestration as the request justifies, and evaluates the result before returning it.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html
      lang="en"
      className={`${display.variable} ${sans.variable} ${mono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        <div className="grain" aria-hidden="true" />
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
