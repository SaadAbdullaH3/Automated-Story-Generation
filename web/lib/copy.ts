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
    case "complete:complete":
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
