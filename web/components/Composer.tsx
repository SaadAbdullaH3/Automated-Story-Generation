"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { api, ApiError } from "@/lib/api";

import styles from "./home.module.css";

// Starting points, written the way a person would actually ask for a film.
const IDEAS = [
  "A clockmaker in a flooded city repairs the hours people lose",
  "The last lighthouse keeper realises the sea has started writing back",
  "Two rival street-food cooks discover they share the same grandmother's recipe",
  "A night train that only stops at stations that no longer exist",
];

const LENGTHS = [20, 30, 45, 60];

export function Composer() {
  const router = useRouter();
  const [prompt, setPrompt] = useState("");
  const [seconds, setSeconds] = useState(30);
  const [scenes, setScenes] = useState(3);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const ready = prompt.trim().length >= 4 && !busy;

  async function start() {
    if (!ready) return;
    setBusy(true);
    setError(null);
    try {
      const run = await api.plan({
        prompt: prompt.trim(),
        target_duration_s: seconds,
        scene_count: scenes,
      });
      router.push(`/studio/?id=${encodeURIComponent(run.project_id)}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't start — is the server running?");
      setBusy(false);
    }
  }

  return (
    <section className={styles.compose}>
      <h1 className={`title ${styles.ask}`}>What happens in your film?</h1>

      <div className={styles.box}>
        <textarea
          className={`title ${styles.prompt}`}
          rows={2}
          value={prompt}
          placeholder="A sentence is enough."
          aria-label="Describe your film"
          onChange={(e) => setPrompt(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) start();
          }}
        />

        <div className={styles.controls}>
          <div className={styles.option} role="radiogroup" aria-label="Length">
            <span className="label">Length</span>
            <div className={styles.segments}>
              {LENGTHS.map((s) => (
                <button
                  key={s}
                  type="button"
                  role="radio"
                  aria-checked={seconds === s}
                  className={seconds === s ? styles.on : undefined}
                  onClick={() => setSeconds(s)}
                >
                  {s}s
                </button>
              ))}
            </div>
          </div>

          <div className={styles.option}>
            <span className="label">Scenes</span>
            <div className={styles.stepper}>
              <button
                type="button"
                aria-label="Fewer scenes"
                onClick={() => setScenes((n) => Math.max(2, n - 1))}
              >
                −
              </button>
              <span className="meta">{scenes}</span>
              <button
                type="button"
                aria-label="More scenes"
                onClick={() => setScenes((n) => Math.min(6, n + 1))}
              >
                +
              </button>
            </div>
          </div>

          <span className={styles.spacer} />
          <button className="btn primary" disabled={!ready} onClick={start}>
            {busy ? "Starting" : "Write the storyboard"}
          </button>
        </div>
      </div>

      {error ? (
        <p className={styles.error} role="alert">
          {error}
        </p>
      ) : (
        <p className={styles.note}>
          You&rsquo;ll see every scene, its lines and a first frame before anything is rendered
          — {scenes} images to plan, and nothing more until you say so.
        </p>
      )}

      {!prompt && (
        <div className={styles.ideas}>
          {IDEAS.map((idea, i) => (
            <button
              key={idea}
              type="button"
              className={`${styles.idea} rise`}
              style={{ "--delay": `${i * 70}ms` } as React.CSSProperties}
              onClick={() => setPrompt(idea)}
            >
              {idea}
            </button>
          ))}
        </div>
      )}
    </section>
  );
}
