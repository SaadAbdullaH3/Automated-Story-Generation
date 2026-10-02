"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import { seconds } from "@/lib/copy";
import type { Film } from "@/lib/types";

import styles from "./home.module.css";

export function FilmGrid() {
  const [films, setFilms] = useState<Film[] | null>(null);

  useEffect(() => {
    api
      .films()
      .then(setFilms)
      .catch(() => setFilms([]));
  }, []);

  if (films === null) return null;

  return (
    <section className={styles.library} aria-labelledby="films">
      <div className={styles.libraryHead}>
        <h2 id="films" className="label">
          Your films
        </h2>
        <span className="meta">{films.length || ""}</span>
      </div>

      {films.length === 0 ? (
        <p className={styles.empty}>Nothing yet. Your first film will appear here.</p>
      ) : (
        <ul className={styles.grid}>
          {films.map((f, i) => (
            <li
              key={f.project_id}
              className="rise"
              style={{ "--delay": `${Math.min(i, 8) * 50}ms` } as React.CSSProperties}
            >
              <Link href={`/studio/?id=${encodeURIComponent(f.project_id)}`} className={styles.film}>
                <div className={styles.poster}>
                  {f.poster_url ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={f.poster_url} alt="" loading="lazy" />
                  ) : (
                    <span className="label">no frame yet</span>
                  )}
                  {f.stage !== "rendered" && <span className={styles.badge}>storyboard</span>}
                </div>
                <h3 className={`title ${styles.filmTitle}`}>{f.title || "Untitled"}</h3>
                <p className="meta">
                  {[
                    f.duration_ms ? seconds(f.duration_ms) : null,
                    f.scene_count ? `${f.scene_count} scenes` : null,
                    `v${f.version}`,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </p>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
