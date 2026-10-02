"use client";

import Link from "next/link";

import { useSession } from "./AuthGate";
import styles from "./topbar.module.css";

export function TopBar() {
  const { user, signOut } = useSession();
  return (
    <header className={styles.bar}>
      <Link href="/" className={`title ${styles.mark}`}>
        Agentic Video Generator
      </Link>
      <span className={styles.spacer} />
      <span className="meta">{user.email}</span>
      <button className="btn quiet small" onClick={signOut}>
        Sign out
      </button>
    </header>
  );
}
