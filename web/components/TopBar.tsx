"use client";

import Link from "next/link";

import { useSession } from "./AuthGate";
import { Logo } from "./Logo";
import styles from "./topbar.module.css";

export function TopBar() {
  const { user, github, notice, dismiss, signOut } = useSession();
  return (
    <>
      <header className={styles.bar}>
        <Link href="/" className={styles.mark} aria-label="Dastango, your films">
          <Logo />
        </Link>
        <span className={styles.spacer} />
        <Link href="/docs/" className={`btn quiet small ${styles.link}`}>
          Docs
        </Link>
        <span className="meta">{user.email}</span>
        {github.enabled && !github.connected && (
          // Linking needs a signed-in account, so it lives here, not on the sign-in page.
          <a className={`btn quiet small ${styles.link}`} href="/api/auth/github/start?mode=connect">
            Connect GitHub
          </a>
        )}
        <button className="btn quiet small" onClick={signOut}>
          Sign out
        </button>
      </header>
      {notice && (
        <p
          className={`${styles.notice} ${notice.kind === "error" ? styles.noticeError : ""}`}
          role={notice.kind === "error" ? "alert" : "status"}
        >
          {notice.text}
          <button className="btn quiet small" onClick={dismiss} aria-label="Dismiss">
            ×
          </button>
        </p>
      )}
    </>
  );
}
