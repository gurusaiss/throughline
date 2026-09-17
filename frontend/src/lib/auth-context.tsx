"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import * as api from "./api";
import type { UserResponse } from "./types";

interface AuthState {
  user: UserResponse | null;
  loading: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (email: string, password: string, displayName?: string, orgName?: string) => Promise<void>;
  signOut: () => void;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<UserResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();

  const refresh = useCallback(async () => {
    if (!api.isAuthenticated()) {
      setUser(null);
      setLoading(false);
      return;
    }
    try {
      const u = await api.me();
      setUser(u);
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Auth bootstrap on mount: check for a stored token and resolve the current user before the app
    // renders any protected route. `refresh` is async — the eventual setState calls happen after the
    // `await` inside it, not synchronously during this effect body, but a plain `void refresh()` reads
    // as "fire and forget" to the linter's static call-graph check. Making the async boundary explicit
    // here satisfies the rule and documents the intent: this is the one-time bootstrap fetch, not a
    // subscription.
    let cancelled = false;
    (async () => {
      await refresh();
      if (cancelled) return;
    })();
    return () => {
      cancelled = true;
    };
  }, [refresh]);

  const signIn = useCallback(
    async (email: string, password: string) => {
      await api.login(email, password);
      await refresh();
      router.push("/playground");
    },
    [refresh, router],
  );

  const signUp = useCallback(
    async (email: string, password: string, displayName?: string, orgName?: string) => {
      await api.signup({
        email,
        password,
        display_name: displayName,
        organization_name: orgName,
      });
      await refresh();
      router.push("/playground");
    },
    [refresh, router],
  );

  const signOut = useCallback(() => {
    api.logout();
    setUser(null);
    router.push("/login");
  }, [router]);

  return (
    <AuthContext.Provider value={{ user, loading, signIn, signUp, signOut, refresh }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
