"use client";

// Follows one project's current job over the WebSocket.
//
// The server replays every event of the job from the start when a socket
// connects, so a page that loads half-way through a render — or reconnects
// after a dropped connection — still sees the whole story. Events carry their
// row id, which is how replays are de-duplicated.
import { useEffect, useState } from "react";

import type { ProgressEvent } from "./types";

function wsUrl(projectId: string): string {
  // In `next dev` the page is on :3000 and the API on :8000.
  const override = process.env.NEXT_PUBLIC_WS_ORIGIN;
  if (override) return `${override}/ws/progress/${projectId}`;
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}/ws/progress/${projectId}`;
}

/** The event that ends a job. A plan ends on a finished storyboard. */
export function isTerminal(ev: ProgressEvent): boolean {
  return (
    ev.phase === "complete" ||
    ev.phase === "cancelled" ||
    (ev.phase === "error" && ev.status === "failed") ||
    (ev.phase === "storyboard" && ev.status === "complete")
  );
}

const MAX_RECONNECTS = 6;

export function useProgress(projectId: string | null, runKey: string | null) {
  const [events, setEvents] = useState<ProgressEvent[]>([]);
  const [finished, setFinished] = useState(false);
  const [signedOut, setSignedOut] = useState(false);

  useEffect(() => {
    if (!projectId || !runKey) return;
    setEvents([]);
    setFinished(false);

    const seen = new Set<number>();
    let socket: WebSocket | null = null;
    let stopped = false;
    let done = false;
    let attempt = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const connect = () => {
      socket = new WebSocket(wsUrl(projectId));
      socket.onopen = () => {
        attempt = 0;
      };
      socket.onmessage = (msg) => {
        let envelope: { type?: string; data?: ProgressEvent };
        try {
          envelope = JSON.parse(msg.data);
        } catch {
          return;
        }
        const ev = envelope.data;
        if (envelope.type !== "event" || !ev || seen.has(ev.id)) return;
        seen.add(ev.id);
        setEvents((prev) => [...prev, ev]);
        if (isTerminal(ev)) {
          done = true;
          setFinished(true);
        }
      };
      socket.onclose = (e) => {
        if (stopped || done) return;
        // 1008: the session ended or the project isn't this account's.
        if (e.code === 1008) {
          setSignedOut(true);
          return;
        }
        // Anything else is a dropped connection: come back, more slowly each
        // time, and the replay fills in whatever was missed.
        if (attempt >= MAX_RECONNECTS) return;
        attempt += 1;
        timer = setTimeout(connect, Math.min(8000, 400 * 2 ** attempt));
      };
    };

    connect();
    return () => {
      stopped = true;
      clearTimeout(timer);
      socket?.close();
    };
  }, [projectId, runKey]);

  return { events, finished, signedOut };
}
