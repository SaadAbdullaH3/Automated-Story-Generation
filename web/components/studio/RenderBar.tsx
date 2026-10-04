"use client";

import { useEffect, useRef, useState } from "react";

import { api, ApiError } from "@/lib/api";
import type { ImageBudget, VoiceCatalogue } from "@/lib/types";

import styles from "./studio.module.css";

export type RenderChoice = { engine: string | null; subtitles: string | null };

type Props = {
  images: ImageBudget | undefined;
  busy: boolean;
  onEngineChange: (engine: string | null) => void;
  onRender: (choice: RenderChoice) => void;
};

/** Everything that is decided at render time, next to the button that spends it. */
export function RenderBar({ images, busy, onEngineChange, onRender }: Props) {
  const [catalogue, setCatalogue] = useState<VoiceCatalogue | null>(null);
  const [languages, setLanguages] = useState<string[]>([]);
  const [engine, setEngine] = useState<string>("");
  const [subtitles, setSubtitles] = useState<string>("English");
  const [sampleNote, setSampleNote] = useState<string | null>(null);
  const [hearing, setHearing] = useState(false);
  const audio = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    api.voices().then(setCatalogue).catch(() => undefined);
    api.languages().then(setLanguages).catch(() => setLanguages(["English"]));
  }, []);

  const current = catalogue?.engines.find((e) => e.name === (engine || catalogue.default));

  async function hear() {
    if (!current) return;
    setHearing(true);
    setSampleNote(null);
    try {
      const sample = await api.previewVoice(current.name, current.voices[0]?.id ?? "");
      if (sample.fell_back) setSampleNote(`${current.label} couldn't speak. That was ${sample.engine}.`);
      if (!audio.current) audio.current = new Audio();
      audio.current.src = sample.url;
      await audio.current.play().catch(() => undefined);
    } catch (err) {
      setSampleNote(err instanceof ApiError ? err.message : "No sample this time.");
    } finally {
      setHearing(false);
    }
  }

  return (
    <div className={styles.renderBar}>
      <div className={styles.choices}>
        <label className="field">
          <span className="label">Voices</span>
          <div className={styles.inline}>
            <select
              className="select"
              value={engine}
              onChange={(e) => {
                setEngine(e.target.value);
                onEngineChange(e.target.value || null);
              }}
            >
              <option value="">
                {catalogue ? `${catalogue.engines.find((e) => e.is_default)?.label ?? "Default"}` : "Default"}
              </option>
              {catalogue?.engines
                .filter((e) => !e.is_default)
                .map((e) => (
                  <option key={e.name} value={e.name} disabled={!e.available}>
                    {e.label}
                    {e.available ? "" : " (unavailable)"}
                  </option>
                ))}
            </select>
            <button type="button" className="btn small" onClick={hear} disabled={!current || hearing}>
              {hearing ? "Listening" : "Hear it"}
            </button>
          </div>
        </label>

        <label className="field">
          <span className="label">Subtitles</span>
          <select className="select" value={subtitles} onChange={(e) => setSubtitles(e.target.value)}>
            <option value="">None</option>
            {languages.map((l) => (
              <option key={l} value={l}>
                {l}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className={styles.spend}>
        {sampleNote ? (
          <p className={styles.sampleNote}>{sampleNote}</p>
        ) : current ? (
          <p className={styles.sampleNote}>{current.summary}</p>
        ) : null}
        <div className={styles.renderRow}>
          {images && (
            <span className="meta">
              rendering draws <b>{images.render}</b> more images
            </span>
          )}
          <button
            className="btn primary"
            disabled={busy}
            onClick={() => onRender({ engine: engine || null, subtitles: subtitles || null })}
          >
            {busy ? "Starting" : "Render this film"}
          </button>
        </div>
      </div>
    </div>
  );
}
