"use client";

import { useRef, useState } from "react";

import { versioned } from "@/lib/api";
import { timecode } from "@/lib/copy";
import type { FilmPlayback } from "@/lib/types";

import styles from "./studio.module.css";

/** The film, with its scenes as chapters you can jump between. */
export function Player({ film }: { film: FilmPlayback }) {
  const video = useRef<HTMLVideoElement | null>(null);
  const [now, setNow] = useState(0);
  const duration = film.duration_ms || 1;

  const current = film.chapters.findIndex(
    (c) => c.start_ms != null && c.end_ms != null && now >= c.start_ms && now < c.end_ms,
  );

  function seek(ms: number | null) {
    if (ms == null || !video.current) return;
    video.current.currentTime = ms / 1000;
    void video.current.play().catch(() => undefined);
  }

  return (
    <section className={styles.watch}>
      <div className={styles.screen}>
        <video
          ref={video}
          src={versioned(film.video_url, film.version)}
          controls
          playsInline
          preload="metadata"
          onTimeUpdate={(e) => setNow(e.currentTarget.currentTime * 1000)}
        >
          {/* The burned-in language is already on the picture; offer the rest. */}
          {film.subtitles
            .filter((t) => !t.burned_in)
            .map((t) => (
              <track
                key={t.code}
                kind="subtitles"
                src={versioned(t.url, film.version)}
                srcLang={t.code}
                label={t.language}
              />
            ))}
        </video>
      </div>

      <ol className={styles.chapters} aria-label="Scenes">
        {film.chapters.map((c, i) => {
          const span = c.start_ms != null && c.end_ms != null ? c.end_ms - c.start_ms : duration / film.chapters.length;
          return (
            <li key={c.scene_id} style={{ flexGrow: Math.max(1, span) }}>
              <button
                className={`${styles.chapter} ${i === current ? styles.chapterOn : ""}`}
                onClick={() => seek(c.start_ms)}
              >
                <span className={styles.chapterFrame}>
                  {c.poster_url && (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={versioned(c.poster_url, film.version)} alt="" />
                  )}
                </span>
                <span className={styles.chapterText}>
                  <span className="meta">{c.start_ms != null ? timecode(c.start_ms) : ""}</span>
                  <span>{c.title}</span>
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
