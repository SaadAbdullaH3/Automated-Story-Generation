"use client";

import { useMemo, useState } from "react";

import { describe, reason } from "@/lib/copy";
import type { CastMember, FilmPlayback, ProgressEvent } from "@/lib/types";

import styles from "./studio.module.css";

type Props = {
  film: FilmPlayback;
  cast: CastMember[];
  /** The edit or revert being followed, if any. */
  events: ProgressEvent[];
  running: boolean;
  elapsed: number | null;
  onSubmit: (query: string) => Promise<void>;
  onStop: () => void;
};

/** Ideas drawn from this film's own scenes and cast, in phrasing that works. */
function suggestionsFor(film: FilmPlayback, cast: CastMember[]): string[] {
  const out: string[] = [];
  const scenes = film.chapters.length;
  if (scenes >= 2) out.push(`Make scene 2 darker`);
  const speaker = cast.find((c) => c.role !== "narrator") ?? cast[0];
  if (speaker) out.push(`Make ${speaker.name}'s voice warmer`);
  out.push("Add tense background music");
  out.push("Apply the noir filter");
  out.push("Speed it up 1.25x");
  return out.slice(0, 4);
}

/** "Change something": one sentence, then the film is made again around it. */
export function EditPanel({ film, cast, events, running, elapsed, onSubmit, onStop }: Props) {
  const [query, setQuery] = useState("");
  const [sending, setSending] = useState(false);
  const ideas = useMemo(() => suggestionsFor(film, cast), [film, cast]);

  const last = events[events.length - 1];
  const understood = events.find((e) => e.phase === "edit" && e.status === "understood");
  const summary = understood?.payload?.summary as string | undefined;
  const failed = last?.phase === "error" && last.status === "failed";
  const done = last?.phase === "complete";
  const busy = running || sending;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const text = query.trim();
    if (!text || busy) return;
    setSending(true);
    try {
      await onSubmit(text);
      setQuery("");
    } finally {
      setSending(false);
    }
  }

  return (
    <section className={styles.edit} aria-labelledby="change-heading">
      <h2 id="change-heading" className="label">
        Change something
      </h2>
      <form className={styles.editForm} onSubmit={submit}>
        <input
          className={`input ${styles.editInput}`}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Say what to change — “make the voices in scene 2 whispered”"
          aria-label="What to change"
          disabled={busy}
          maxLength={300}
        />
        <button className="btn primary" type="submit" disabled={busy || !query.trim()}>
          Make the change
        </button>
      </form>

      {/* Kept after a refusal too: that is when an example helps most. */}
      {!busy && (
        <div className={styles.ideas}>
          {ideas.map((idea) => (
            <button key={idea} type="button" className={styles.idea} onClick={() => setQuery(idea)}>
              {idea}
            </button>
          ))}
        </div>
      )}

      {(busy || failed || done) && last && (
        <div className={styles.editStatus} aria-live="polite">
          {summary && <p className={styles.understood}>{summary}</p>}
          <p className={styles.editLine}>
            {running && <span className="pip" />}
            <span className={failed ? styles.statusFailed : undefined}>
              {failed ? `Couldn’t make that change: ${reason(last.message)}` : describe(last)}
            </span>
            {running && elapsed != null && <span className="meta">· {elapsed}s</span>}
            {running && (
              <button className="btn quiet small" type="button" onClick={onStop}>
                Stop
              </button>
            )}
          </p>
          {failed && <p className={styles.editNote}>Nothing was changed — the film is as it was.</p>}
        </div>
      )}
    </section>
  );
}
