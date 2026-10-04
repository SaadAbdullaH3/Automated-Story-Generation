// The only place the interface talks to the server. Same origin in production
// (FastAPI serves this app), so cookies ride along and there is no CORS.

import type {
  AuthStatus,
  Card,
  Film,
  FilmPlayback,
  FilmVersion,
  JobSnapshot,
  RunResponse,
  Storyboard,
  User,
  VoiceCatalogue,
} from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      credentials: "same-origin",
      ...init,
      headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
    });
  } catch {
    // No answer at all: the server is down or restarting. Say so, rather than
    // letting every caller guess with its own vague "couldn't…".
    throw new ApiError(0, "Can't reach the server. Check it's running, then try again.");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      /* not JSON — keep the status text */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export type PlanOptions = {
  prompt: string;
  target_duration_s: number;
  scene_count: number;
};

export type RenderOptions = {
  with_bgm?: boolean;
  with_subtitles?: boolean;
  subtitle_language?: string;
  burn_subtitles?: boolean;
  tts_engine?: string | null;
};

export type SceneEdit = {
  title?: string;
  visual_prompt?: string;
  dialogue?: Record<string, string>;
};

export const api = {
  // accounts
  authStatus: () => request<AuthStatus>("/api/auth/status"),
  login: (email: string, password: string) =>
    post<{ user: User }>("/api/auth/login", { email, password }),
  register: (email: string, password: string) =>
    post<{ user: User }>("/api/auth/register", { email, password }),
  logout: () => post<{ ok: boolean }>("/api/auth/logout"),

  // library
  films: () => request<Film[]>("/api/projects/"),

  // the loop: plan -> storyboard -> render -> watch
  plan: (opts: PlanOptions) => post<RunResponse>("/api/pipeline/plan", { ...opts, with_preview: true }),
  storyboard: (pid: string, engine?: string | null) =>
    request<Storyboard>(
      `/api/pipeline/storyboard/${pid}${engine ? `?engine=${encodeURIComponent(engine)}` : ""}`,
    ),
  editScene: (pid: string, sceneId: string, edit: SceneEdit) =>
    request<Storyboard>(`/api/pipeline/storyboard/${pid}/${sceneId}`, {
      method: "PATCH",
      body: JSON.stringify(edit),
    }),
  render: (pid: string, opts: RenderOptions) =>
    post<RunResponse>(`/api/pipeline/render/${pid}`, opts),
  film: (pid: string) => request<FilmPlayback>(`/api/pipeline/film/${pid}`),
  jobStatus: (pid: string) => request<JobSnapshot>(`/api/pipeline/status/${pid}`),
  cancel: (jobId: string) => post<{ status: string }>(`/api/jobs/${jobId}/cancel`),

  // after the render: change it in a sentence, or go back
  edit: (pid: string, query: string) => post<RunResponse>(`/api/edit/${pid}`, { query }),
  versions: (pid: string) => request<FilmVersion[]>(`/api/history/${pid}/film`),
  goBack: (pid: string, version: number) =>
    post<{ version: number }>(`/api/history/${pid}/revert/${version}`),

  // options
  languages: () => request<string[]>("/api/pipeline/languages"),
  voices: () => request<VoiceCatalogue>("/api/voices/"),
  previewVoice: (engine: string, voice: string) =>
    post<{ url: string; engine: string; fell_back: boolean }>("/api/voices/preview", {
      engine,
      voice,
    }),
};

/** A file that keeps its name across versions, made distinct per version so
 * the browser doesn't play the cached cut after an edit. */
export function versioned(url: string, version: number): string {
  return `${url}${url.includes("?") ? "&" : "?"}v=${version}`;
}

export type { Card };
