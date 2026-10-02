// What a creator reads while their film is being made. The pipeline speaks in
// phases and nodes; nobody making a film should have to.
import type { ProgressEvent } from "./types";

export function describe(ev: ProgressEvent | undefined): string {
  if (!ev) return "Starting";
  const scenes = (ev.payload?.scenes as unknown[] | undefined)?.length;
  switch (`${ev.phase}:${ev.status}`) {
    case "story:started":
      return "Writing the script";
    case "story:complete":
      return "Script written";
    case "storyboard:script":
      return `${scenes ?? "The"} scenes written — drawing them now`;
    case "storyboard:started":
      return "Drawing the storyboard";
    case "storyboard:frame":
      return ev.message;
    case "storyboard:complete":
      return "Storyboard ready";
    case "audio:started":
      return "Recording the voices";
    case "audio:complete":
      return "Voices recorded";
    case "video:started":
      return "Drawing the shots and cutting the film";
    case "video:complete":
      return "Film cut";
    case "edit:started":
      return "Reading your change";
    case "edit:understood":
      // What it understood is shown beside this; the line says what's next.
      return "Planning the change";
    case "edit:step":
    case "revert:started":
      return ev.message;
    case "complete:complete":
      if (ev.payload?.kind === "edit") return "Change made";
      if (ev.payload?.kind === "revert") return ev.message;
      return "Your film is ready";
    case "cancelled:cancelled":
      return "Stopped";
    case "error:retrying":
      return "Hit a snag — trying again";
    case "error:failed":
      return "This one didn't make it";
  }
  return ev.message || "Working";
}

/** A failure's reason without "EditFailed: ValueError: " stacked in front. */
export function reason(message: string | undefined | null): string {
  let text = (message ?? "").trim();
  for (;;) {
    const m = /^([A-Za-z_]\w*(?:Error|Exception|Failed)): /.exec(text);
    if (!m) return text;
    text = text.slice(m[0].length);
  }
}

/** "just now", "4 min ago", "yesterday" — from the server's naive UTC. */
export function ago(iso: string, now: number): string {
  const t = Date.parse(iso.endsWith("Z") ? iso : `${iso}Z`);
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return s < 172800 ? "yesterday" : `${Math.round(s / 86400)} days ago`;
}

/** Seconds since the first event, from the server's own timestamps. */
export function elapsedSeconds(events: ProgressEvent[], now: number): number | null {
  const first = events.find((e) => e.created_at)?.created_at;
  if (!first) return null;
  // The server writes naive UTC; say so, or the browser assumes local time.
  const started = Date.parse(first.endsWith("Z") ? first : `${first}Z`);
  return Number.isNaN(started) ? null : Math.max(0, Math.round((now - started) / 1000));
}

export function seconds(ms: number | null | undefined): string {
  if (ms == null) return "";
  return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)}s`;
}

export function timecode(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}
