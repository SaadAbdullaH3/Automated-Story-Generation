"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { api, ApiError, versioned, type SceneEdit } from "@/lib/api";
import { describe, elapsedSeconds } from "@/lib/copy";
import type {
  Card,
  CastMember,
  FilmPlayback,
  FilmVersion,
  ImageBudget,
  Storyboard,
} from "@/lib/types";
import { useProgress } from "@/lib/useProgress";

import { EditPanel } from "./EditPanel";
import { Player } from "./Player";
import { RenderBar, type RenderChoice } from "./RenderBar";
import { SceneCard } from "./SceneCard";
import { Versions } from "./Versions";
import styles from "./studio.module.css";

type Followed = { id: string; kind: string };

/** Jobs that change a finished film rather than make one. */
const CHANGES = new Set(["edit", "revert"]);

/** What the strip shows while a plan is still being drawn, built from events. */
type Streamed = {
  prompt?: string;
  title: string;
  logline: string;
  cards: Card[];
  cast: CastMember[];
  images?: ImageBudget;
};

export function Studio() {
  const router = useRouter();
  const pid = useSearchParams().get("id");

  const [board, setBoard] = useState<Storyboard | null>(null);
  const [film, setFilm] = useState<FilmPlayback | null>(null);
  const [job, setJob] = useState<Followed | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [engine, setEngine] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [showBoard, setShowBoard] = useState(false);
  const [askedFor, setAskedFor] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [versions, setVersions] = useState<FilmVersion[]>([]);
  const [restoring, setRestoring] = useState<number | null>(null);
  // A finished change whose outcome no longer describes what's playing.
  const [settled, setSettled] = useState<string | null>(null);

  const { events, finished, signedOut } = useProgress(pid, job?.id ?? null);

  const refresh = useCallback(
    async (voiceEngine: string | null = engine) => {
      if (!pid) return;
      const [b, f, v] = await Promise.all([
        api.storyboard(pid, voiceEngine).catch(() => null),
        api.film(pid).catch(() => null),
        api.versions(pid).catch(() => [] as FilmVersion[]),
      ]);
      setBoard(b);
      setFilm(f);
      setVersions(v);
    },
    [pid, engine],
  );

  // First load: what is this project, and is anything running on it?
  useEffect(() => {
    if (!pid) {
      router.replace("/");
      return;
    }
    let cancelled = false;
    (async () => {
      const snap = await api.jobStatus(pid).catch(() => null);
      if (!cancelled && snap?.prompt) setAskedFor(snap.prompt);
      if (!cancelled && snap?.job_id && (snap.status === "queued" || snap.status === "running")) {
        setJob({ id: snap.job_id, kind: snap.kind ?? "plan" });
      }
      await refresh(null);
      if (!cancelled) setLoaded(true);
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid]);

  // When the job we're following ends, read back what it settled into.
  useEffect(() => {
    if (finished) void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [finished]);

  useEffect(() => {
    if (signedOut) window.location.assign("/");
  }, [signedOut]);

  const running = job !== null && !finished;
  const changing = job !== null && CHANGES.has(job.kind);
  // A second-by-second clock while something runs; otherwise enough to keep
  // "4 min ago" honest.
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), running ? 1000 : 30_000);
    return () => clearInterval(t);
  }, [running]);

  // The plan streams the script first and then each frame as it is drawn.
  const streamed = useMemo<Streamed | null>(() => {
    const script = events.find((e) => e.phase === "storyboard" && e.status === "script");
    if (!script?.payload) return null;
    const urls = new Map<string, string>();
    for (const e of events) {
      if (e.phase === "storyboard" && e.status === "frame" && e.payload) {
        urls.set(String(e.payload.scene_id), String(e.payload.preview_url));
      }
    }
    const p = script.payload as unknown as Streamed & { scenes: Card[] };
    return {
      prompt: p.prompt,
      title: p.title,
      logline: p.logline,
      cast: p.cast ?? [],
      images: p.images,
      cards: p.scenes.map((c) => ({ ...c, preview_url: urls.get(c.scene_id) ?? null })),
    };
  }, [events]);

  const last = events[events.length - 1];
  const failed = last?.phase === "error" && last.status === "failed";

  type Mode = "loading" | "planning" | "storyboard" | "rendering" | "watch" | "missing";
  let mode: Mode;
  if (!loaded) mode = "loading";
  else if (running && job?.kind === "plan") mode = "planning";
  else if (running && !changing) mode = "rendering";
  // An edit keeps you with the film: it plays on while the change is made.
  else if (film && (!showBoard || running)) mode = "watch";
  else if (board || streamed) mode = "storyboard";
  else mode = "missing";

  const cards: Card[] = board?.frames ?? streamed?.cards ?? [];
  const title = board?.title || streamed?.title || film?.title || "";
  const prompt = board?.prompt || streamed?.prompt || askedFor || "";
  const images = board?.images ?? streamed?.images;
  // The scene being drawn right now: the first one without a frame.
  const drawingIndex = mode === "planning" ? cards.findIndex((c) => !c.preview_url) : -1;
  const elapsed = elapsedSeconds(events, now);

  async function render(choice: RenderChoice) {
    if (!pid) return;
    setStarting(true);
    setProblem(null);
    try {
      const run = await api.render(pid, {
        tts_engine: choice.engine,
        with_subtitles: Boolean(choice.subtitles),
        subtitle_language: choice.subtitles ?? "English",
        burn_subtitles: true,
      });
      setShowBoard(false);
      setJob({ id: run.job_id, kind: "render" });
    } catch (err) {
      setProblem(err instanceof ApiError ? err.message : "Couldn't start the render.");
    } finally {
      setStarting(false);
    }
  }

  async function saveScene(sceneId: string, edit: SceneEdit) {
    if (!pid) return;
    try {
      await api.editScene(pid, sceneId, edit);
      await refresh();
    } catch (err) {
      setProblem(err instanceof ApiError ? err.message : "That edit didn't save.");
      throw err;
    }
  }

  async function stop() {
    if (job) await api.cancel(job.id).catch(() => undefined);
  }

  async function change(query: string) {
    if (!pid) return;
    setProblem(null);
    try {
      const run = await api.edit(pid, query);
      setJob({ id: run.job_id, kind: "edit" });
    } catch (err) {
      setProblem(err instanceof ApiError ? err.message : "Couldn't start that change.");
    }
  }

  async function goBack(version: number) {
    if (!pid) return;
    setRestoring(version);
    setProblem(null);
    if (job) setSettled(job.id);
    try {
      await api.goBack(pid, version);
      await refresh();
    } catch (err) {
      setProblem(err instanceof ApiError ? err.message : "Couldn't go back to that version.");
    } finally {
      setRestoring(null);
    }
  }

  if (mode === "loading") return <div className={styles.loading} aria-busy="true" />;

  if (mode === "missing") {
    return (
      <section className={styles.missing}>
        <h1 className="title">This film isn&rsquo;t here.</h1>
        <p>It may belong to another account, or it was never made.</p>
        <Link className="btn" href="/">
          Start a new one
        </Link>
      </section>
    );
  }

  return (
    <div className={styles.studio}>
      <header className={styles.head}>
        <p className="label">{title || " "}</p>
        <h1 className={`title ${styles.prompt}`}>{prompt || title || "Writing the script"}</h1>

        {/* A change to a finished film reports beside the film instead. */}
        {!changing && (running || failed || (finished && last)) && (
          <p className={styles.status} aria-live="polite">
            {running && <span className="pip" />}
            <span className={failed ? styles.statusFailed : undefined}>
              {failed ? `${describe(last)}: ${last?.message}` : describe(last)}
            </span>
            {running && elapsed != null && <span className="meta">· {elapsed}s</span>}
            {running && (
              <button className="btn quiet small" onClick={stop}>
                Stop
              </button>
            )}
          </p>
        )}
        {problem && (
          <p className={styles.problem} role="alert">
            {problem}
          </p>
        )}
      </header>

      {mode === "watch" && film ? (
        <>
          {/* Keyed by version: a new cut reloads the player and its tracks. */}
          <Player key={film.version} film={film} />
          <div className={styles.watchActions}>
            <a className="btn primary" href={versioned(film.video_url, film.version)} download>
              Download
            </a>
            <button className="btn" onClick={() => setShowBoard(true)} disabled={running}>
              Back to the storyboard
            </button>
            <span className="meta">
              {film.width}×{film.height} · {film.fps}fps · v{film.version}
            </span>
          </div>
          <div className={styles.afterRender}>
            <EditPanel
              film={film}
              cast={board?.cast ?? []}
              events={changing && job?.id !== settled ? events : []}
              running={running && changing}
              elapsed={changing ? elapsed : null}
              onSubmit={change}
              onStop={stop}
            />
            <Versions
              versions={versions}
              locked={running}
              restoring={restoring}
              now={now}
              onGoBack={goBack}
            />
          </div>
        </>
      ) : (
        <>
          <div
            className={styles.strip}
            style={{ "--count": Math.max(1, cards.length) } as React.CSSProperties}
          >
            {cards.map((card, i) => (
              <SceneCard
                key={`${card.scene_id}-${card.preview_url ?? "pending"}-${board?.version ?? 0}`}
                card={card}
                number={i + 1}
                delay={i * 90}
                active={i === drawingIndex}
                editable={mode === "storyboard" && Boolean(board)}
                onSave={(edit) => saveScene(card.scene_id, edit)}
              />
            ))}
          </div>

          {mode === "storyboard" && board && (
            <RenderBar
              images={images}
              busy={starting}
              onEngineChange={(e) => {
                setEngine(e);
                void refresh(e);
              }}
              onRender={render}
            />
          )}
          {mode === "storyboard" && film && (
            <button className="btn quiet" onClick={() => setShowBoard(false)}>
              Back to the film
            </button>
          )}
        </>
      )}
    </div>
  );
}
