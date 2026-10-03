import Link from "next/link";

import { DocsNav } from "@/components/docs/DocsNav";
import styles from "@/components/docs/docs.module.css";
import { OnThisPage } from "@/components/docs/OnThisPage";
import { Logo } from "@/components/Logo";
import { REPO_URL } from "@/lib/docs";

// The documentation is public: no sign-in, so anyone can read how it works
// before making an account.
export default function DocsLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className={styles.shell}>
      <header className={styles.top}>
        <Link href="/docs/" className={styles.brand} aria-label="Dastango documentation">
          <Logo size={26} />
          <span className={`label ${styles.brandTag}`}>Docs</span>
        </Link>
        <span className={styles.topSpacer} />
        <a className={`btn quiet small ${styles.hideSmall}`} href={REPO_URL} target="_blank" rel="noreferrer">
          GitHub
        </a>
        <a className="btn primary small" href="/">
          Open Dastango
        </a>
      </header>

      <div className={styles.layout}>
        <aside className={styles.side}>
          <DocsNav shortcut />
        </aside>
        <main className={styles.main}>
          <details className={styles.mobileNav}>
            <summary>Browse the docs</summary>
            <DocsNav />
          </details>
          {children}
          <footer className={styles.footer}>
            <span>Dastango</span>
            <a href={REPO_URL} target="_blank" rel="noreferrer">
              Source on GitHub
            </a>
            <a href="/api/docs">API reference</a>
          </footer>
        </main>
        <aside className={styles.aside}>
          <OnThisPage />
        </aside>
      </div>
    </div>
  );
}
