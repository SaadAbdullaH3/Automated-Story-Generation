"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { DOC_PAGES, docHref, SECTIONS } from "@/lib/docs";

import styles from "./docs.module.css";

/** The sidebar: every page by section, a filter, and where you are. */
export function DocsNav({ shortcut = false }: { shortcut?: boolean }) {
  const pathname = usePathname();
  const [query, setQuery] = useState("");
  const input = useRef<HTMLInputElement>(null);

  // "/" jumps to the filter, as on most documentation sites.
  useEffect(() => {
    if (!shortcut) return;
    function onKey(e: KeyboardEvent) {
      const typing = e.target instanceof HTMLElement && e.target.closest("input, textarea, select");
      if (e.key === "/" && !typing) {
        e.preventDefault();
        input.current?.focus();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [shortcut]);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return DOC_PAGES;
    return DOC_PAGES.filter((p) =>
      [p.title, p.summary, p.section, ...p.keywords].join(" ").toLowerCase().includes(q),
    );
  }, [query]);

  const here = (pathname || "/docs/").replace(/\/?$/, "/");

  return (
    <nav className={styles.nav} aria-label="Documentation">
      <label className={styles.filter}>
        <span className="sr-only">Filter pages</span>
        <input
          ref={input}
          className="input"
          type="search"
          placeholder="Filter pages"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {shortcut && (
          <kbd className={styles.kbd} aria-hidden="true">
            /
          </kbd>
        )}
      </label>

      {SECTIONS.map((section) => {
        const pages = matches.filter((p) => p.section === section);
        if (!pages.length) return null;
        return (
          <div key={section} className={styles.navGroup}>
            <p className="label">{section}</p>
            <ul>
              {pages.map((p) => {
                const href = docHref(p.slug);
                const current = href === here;
                return (
                  <li key={p.slug}>
                    <Link
                      href={href}
                      className={`${styles.navLink} ${current ? styles.navOn : ""}`}
                      aria-current={current ? "page" : undefined}
                    >
                      {p.title}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        );
      })}
      {!matches.length && <p className={styles.navEmpty}>Nothing matches “{query}”.</p>}
    </nav>
  );
}
