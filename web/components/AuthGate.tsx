"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";

import { api, ApiError } from "@/lib/api";
import type { AuthStatus, User } from "@/lib/types";

import styles from "./auth.module.css";
import { Logo } from "./Logo";

/** Something the GitHub round trip came back with, shown once. */
export type Notice = { text: string; kind: "ok" | "error" };

type Session = {
  user: User;
  github: { enabled: boolean; connected: boolean };
  notice: Notice | null;
  dismiss: () => void;
  signOut: () => Promise<void>;
};
const SessionContext = createContext<Session | null>(null);

export function useSession(): Session {
  const session = useContext(SessionContext);
  if (!session) throw new Error("useSession outside <AuthGate>");
  return session;
}

/** The GitHub callback lands on `/?auth_error=…` or `/?connected=github`.
 * Read it once and take it out of the address bar, so a reload doesn't
 * announce it again. */
function takeRedirectOutcome(): Notice | null {
  const params = new URLSearchParams(window.location.search);
  const error = params.get("auth_error");
  const connected = params.get("connected");
  if (!error && !connected) return null;
  params.delete("auth_error");
  params.delete("connected");
  const rest = params.toString();
  window.history.replaceState(null, "", window.location.pathname + (rest ? `?${rest}` : ""));
  return error ? { text: error, kind: "error" } : { text: "GitHub is connected.", kind: "ok" };
}

/** Nothing renders until we know who this is; signed out, the form does. */
export function AuthGate({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<AuthStatus | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);

  useEffect(() => {
    setNotice(takeRedirectOutcome());
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
  if (!user) {
    return (
      <SignIn
        status={status}
        problem={notice?.kind === "error" ? notice.text : null}
        onSignedIn={setUser}
      />
    );
  }

  const github = status.github ?? { enabled: false, connected: false };
  return (
    <SessionContext.Provider
      value={{
        user,
        github: notice?.kind === "ok" ? { ...github, connected: true } : github,
        notice,
        dismiss: () => setNotice(null),
        signOut,
      }}
    >
      {children}
    </SessionContext.Provider>
  );
}

function GitHubMark() {
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" fill="currentColor">
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z" />
    </svg>
  );
}

function SignIn({
  status,
  problem,
  onSignedIn,
}: {
  status: AuthStatus;
  problem: string | null;
  onSignedIn: (u: User) => void;
}) {
  const setup = status.needs_setup;
  const [mode, setMode] = useState<"login" | "register">(setup ? "register" : "login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(problem);
  const [busy, setBusy] = useState(false);
  const registering = mode === "register";
  const withGitHub = Boolean(status.github?.enabled);

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
      setError(err instanceof ApiError ? err.message : "That didn't work. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className={styles.gate}>
      <form className={`${styles.card} rise`} onSubmit={submit}>
        <Logo size={34} />
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

        {withGitHub && (
          <>
            {/* A plain link: GitHub's page has to be a full navigation, not a fetch. */}
            <a className={`btn ${styles.github}`} href="/api/auth/github/start">
              <GitHubMark />
              Continue with GitHub
            </a>
            <p className={styles.or}>
              <span>or with an email</span>
            </p>
          </>
        )}

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
        <p className={styles.docs}>
          New here? <a href="/docs/">Read how it works</a>
        </p>
      </form>
    </main>
  );
}
