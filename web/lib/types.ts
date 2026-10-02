// Shapes of what the FastAPI backend returns. Kept to what the interface reads.

export type User = {
  id: string;
  email: string;
  role: "user" | "admin";
};

export type AuthStatus = {
  authenticated: boolean;
  user: User | null;
  needs_setup: boolean;
  signups_allowed: boolean;
  min_password_length: number;
  /** GitHub sign-in: set up on this server, and linked to this account. */
  github?: { enabled: boolean; connected: boolean };
};

export type Line = {
  line_id: string;
  character: string;
  voice: string;
  text: string;
};

/** One scene card. Streamed during planning, read back from the storyboard. */
export type Card = {
  scene_id: string;
  index: number;
  title: string;
  setting?: string;
  tone: string;
  move: string;
  estimated_ms: number;
  lines: Line[];
  preview_url?: string | null;
  visual_prompt?: string;
};

export type CastMember = {
  character_id: string;
  name: string;
  role: string;
  voice: string;
};

export type ImageBudget = { plan: number; render: number };

export type Storyboard = {
  project_id: string;
  prompt: string;
  title: string;
  logline: string;
  frames: Card[];
  cast: CastMember[];
  images: ImageBudget;
  stage: "draft" | "storyboard" | "rendered";
  version: number;
};

export type Film = {
  project_id: string;
  title: string;
  prompt: string;
  version: number;
  updated_at: string;
  video_url: string | null;
  poster_url: string | null;
  stage: string;
  scene_count: number;
  duration_ms: number | null;
};

export type Chapter = {
  scene_id: string;
  index: number;
  title: string;
  tone: string;
  start_ms: number | null;
  end_ms: number | null;
  poster_url: string | null;
};

export type SubtitleTrack = {
  language: string;
  code: string;
  url: string;
  burned_in: boolean;
};

export type FilmPlayback = {
  project_id: string;
  title: string;
  logline: string;
  video_url: string;
  duration_ms: number;
  width: number;
  height: number;
  fps: number;
  version: number;
  chapters: Chapter[];
  subtitles: SubtitleTrack[];
};

export type ProgressEvent = {
  id: number;
  job_id: string;
  project_id: string;
  phase: string;
  status: string;
  message: string;
  progress: number;
  payload: Record<string, unknown> | null;
  created_at: string | null;
};

/** One version of a finished film, in the creator's words. */
export type FilmVersion = {
  version: number;
  created_at: string;
  current: boolean;
  kind: "render" | "edit" | "revert" | "rerun" | "other";
  label: string;
  detail: string;
  restored?: number | null;
};

export type JobSnapshot = {
  project_id: string;
  job_id?: string;
  kind?: "plan" | "render" | "run_full" | "rerun_phase" | "edit" | "revert";
  prompt?: string | null;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled" | "unknown";
  phase?: string;
  progress?: number;
  message?: string;
  error?: string | null;
};

export type RunResponse = {
  project_id: string;
  job_id: string;
  status: string;
  websocket: string;
};

export type Voice = { id: string; label: string; gender: string };

export type VoiceEngine = {
  name: string;
  label: string;
  summary: string;
  open_source: boolean;
  needs_network: boolean;
  available: boolean;
  unavailable_reason: string | null;
  is_default: boolean;
  voices: Voice[];
};

export type VoiceCatalogue = { default: string; engines: VoiceEngine[] };
