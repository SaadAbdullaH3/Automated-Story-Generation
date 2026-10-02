"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";

import { api, ApiError } from "@/lib/api";
import type { AuthStatus, User } from "@/lib/types";

import styles from "./auth.module.css";

type Session = { user: User; signOut: () => Promise<void> };
const SessionContext = createContext<Session | null>(null);

export function useSession(): Session {
  const session = useContext(SessionContext);
  if (!session) throw new Error("useSession outside <AuthGate>");
  return session;
}

/** Nothing renders until we know who this is; signed out, the form does. */
export function AuthGate({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<AuthStatus | null>(null);
  const [user, setUser] = useState<User | null>(null);

  useEffect(() => {
    api
      .authStatus()
      .then((s) => {
        setStatus(s);
        setUser(s.user);
      })
      .catch(() =>
        setStatus({
          authenticated: false,
          user: null,
          needs_setup: false,
          signups_allowed: false,
          min_password_length: 10,
        }),
      );
  }, []);

  const signOut = useCallback(async () => {
    await api.logout().catch(() => undefined);
    // A full reload is the surest way nothing of the last account stays on screen.
    window.location.assign("/");
  }, []);

  if (!status) return <div className={styles.blank} aria-busy="true" />;
  if (!user) return <SignIn status={status} onSignedIn={setUser} />;

  return <SessionContext.Provider value={{ user, signOut }}>{children}</SessionContext.Provider>;
}

function SignIn({ status, onSignedIn }: { status: AuthStatus; onSignedIn: (u: User) => void }) {
  const setup = status.needs_setup;
  const [mode, setMode] = useState<"login" | "register">(setup ? "register" : "login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const registering = mode === "register";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const { user } = registering
        ? await api.register(email.trim(), password)
        : await api.login(email.trim(), password);
      onSignedIn(user);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "That didn't work — try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className={styles.gate}>
      <form className={`${styles.card} rise`} onSubmit={submit}>
        <p className="label">Agentic Video Generator</p>
        <h1 className={`title ${styles.heading}`}>
          {setup ? "Make the first account" : registering ? "Make an account" : "Welcome back"}
        </h1>
        <p className={styles.lede}>
          {setup
            ? "Nobody has signed up yet, so this account runs the place."
            : registering
              ? `Any address, and a password of at least ${status.min_password_length} characters.`
              : "One sentence in, a short film out."}
        </p>

        <label className="field">
          <span className="label">Email</span>
          <input
            className="input"
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </label>
        <label className="field">
          <span className="label">Password</span>
          <input
            className="input"
            type="password"
            autoComplete={registering ? "new-password" : "current-password"}
            minLength={registering ? status.min_password_length : undefined}
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>

        {error && (
          <p className={styles.error} role="alert">
            {error}
          </p>
        )}

        <button className="btn primary" type="submit" disabled={busy}>
          {busy ? "One moment" : registering ? "Make the account" : "Sign in"}
        </button>

        {!setup && status.signups_allowed && (
          <button
            type="button"
            className="btn quiet"
            onClick={() => {
              setMode(registering ? "login" : "register");
              setError(null);
            }}
          >
            {registering ? "I already have an account" : "Make an account instead"}
          </button>
        )}
      </form>
    </main>
  );
}
