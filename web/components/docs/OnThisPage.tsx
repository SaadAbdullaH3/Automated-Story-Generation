"use client";

import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import styles from "./docs.module.css";

type Heading = { id: string; text: string };

/** The current page's sections, with the one being read highlighted. */
export function OnThisPage() {
  const pathname = usePathname();
  const [headings, setHeadings] = useState<Heading[]>([]);
  const [active, setActive] = useState<string | null>(null);

  useEffect(() => {
    const els = Array.from(document.querySelectorAll<HTMLElement>("h2[data-toc]"));
    setHeadings(els.map((el) => ({ id: el.id, text: el.textContent ?? "" })));
    setActive(els[0]?.id ?? null);
    if (!els.length) return;
    // The section whose heading most recently crossed the top third is "here".
    const seen = new IntersectionObserver(
      (entries) => {
        const first = entries.find((e) => e.isIntersecting);
        if (first) setActive(first.target.id);
      },
      { rootMargin: "0px 0px -66% 0px" },
    );
    els.forEach((el) => seen.observe(el));
    return () => seen.disconnect();
  }, [pathname]);

  if (headings.length < 2) return null;
  return (
    <nav className={styles.toc} aria-label="On this page">
      <p className="label">On this page</p>
      <ul>
        {headings.map((h) => (
          <li key={h.id}>
            <a href={`#${h.id}`} className={h.id === active ? styles.tocOn : undefined}>
              {h.text}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}
