import Link from "next/link";

import { docBySlug, docHref, neighbours } from "@/lib/docs";

import styles from "./docs.module.css";

/** The frame every documentation page shares: title, summary, body, pager. */
export function DocsArticle({ slug, children }: { slug: string; children: React.ReactNode }) {
  const page = docBySlug(slug);
  const { prev, next } = neighbours(slug);
  return (
    <article className={styles.article}>
      <header className={styles.head}>
        <p className="label">{page?.section}</p>
        <h1 className={`title ${styles.h1}`}>{page?.title}</h1>
        <p className={styles.lede}>{page?.summary}</p>
      </header>
      <div className={styles.prose}>{children}</div>
      <nav className={styles.pager} aria-label="More pages">
        {prev ? (
          <Link href={docHref(prev.slug)} className={styles.pagerLink}>
            <span className="label">Previous</span>
            <span>{prev.title}</span>
          </Link>
        ) : (
          <span />
        )}
        {next && (
          <Link href={docHref(next.slug)} className={`${styles.pagerLink} ${styles.pagerNext}`}>
            <span className="label">Next</span>
            <span>{next.title}</span>
          </Link>
        )}
      </nav>
    </article>
  );
}

/** A section heading the "On this page" list picks up. */
export function H2({ id, children }: { id: string; children: React.ReactNode }) {
  return (
    <h2 id={id} data-toc className={`title ${styles.h2}`}>
      <a href={`#${id}`} className={styles.anchor}>
        {children}
      </a>
    </h2>
  );
}

/** A short aside: a tip, or something worth knowing before you start. */
export function Note({ kind = "tip", children }: { kind?: "tip" | "heads-up"; children: React.ReactNode }) {
  return (
    <aside className={`${styles.note} ${kind === "heads-up" ? styles.noteHeadsUp : ""}`}>
      <p className="label">{kind === "tip" ? "Tip" : "Good to know"}</p>
      <div>{children}</div>
    </aside>
  );
}

/** Numbered steps, each with a short title. */
export function Steps({ children }: { children: React.ReactNode }) {
  return <ol className={styles.steps}>{children}</ol>;
}

export function Step({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <li className={styles.step}>
      <p className={styles.stepTitle}>{title}</p>
      <div>{children}</div>
    </li>
  );
}

/** Something to type, shown the way the interface shows it. */
export function Say({ children }: { children: React.ReactNode }) {
  return <q className={styles.say}>{children}</q>;
}

/** A row of linked cards — used on the introduction. */
export function CardLinks({ items }: { items: { href: string; title: string; text: string }[] }) {
  return (
    <div className={styles.cards}>
      {items.map((i) => (
        <Link key={i.href} href={i.href} className={styles.card}>
          <span className={styles.cardTitle}>{i.title}</span>
          <span className={styles.cardText}>{i.text}</span>
        </Link>
      ))}
    </div>
  );
}
