/* Agentic Video Generator — single-page UI */

const $ = (id) => document.getElementById(id);

const state = {
  projectId: null,
  jobId: null,
  ws: null,
  intent: null,
  storyboard: null,
  voices: { default: "", engines: [] },
};

const PHASES = ["story", "storyboard", "audio", "video"];

// ---- helpers ---------------------------------------------------------------

function appendLog(msg) {
  const log = $("log");
  const ts = new Date().toLocaleTimeString();
  log.textContent += `[${ts}] ${msg}\n`;
  log.scrollTop = log.scrollHeight;
}

function setPhaseProgress(phase, status, progress) {
  const el = $(`phase-${phase}`);
  if (!el) return;
  el.classList.remove("active", "complete", "failed");
  if (status === "complete") el.classList.add("complete");
  else if (status === "failed") el.classList.add("failed");
  else if (status === "started" || status === "running") el.classList.add("active");
  const bar = el.querySelector(".bar span");
  if (bar) bar.style.width = `${Math.min(100, Math.max(0, (progress || 0) * 100))}%`;
}

function resetPhases() {
  PHASES.forEach((p) => {
    const el = $(`phase-${p}`);
    if (!el) return;
    el.classList.remove("active", "complete", "failed");
    el.querySelector(".bar span").style.width = "0%";
  });
}

function setRerunButtons(enabled) {
  ["rerunStory", "rerunAudio", "rerunVideo"].forEach((id) => {
    $(id).disabled = !enabled;
  });
  $("applyEdit").disabled = !enabled;
  $("classifyEdit").disabled = !enabled;
}

// ---- storyboard ------------------------------------------------------------

function promptBody() {
  return {
    prompt: $("prompt").value.trim(),
    target_duration_s: parseInt($("duration").value, 10),
    scene_count: parseInt($("scenes").value, 10),
  };
}

// "" means: let config/providers.yaml decide.
function chosenEngine() {
  return $("voice-engine").value || null;
}

async function startPlan() {
  const body = promptBody();
  if (!body.prompt) {
    alert("Enter a prompt first.");
    return;
  }
  resetPhases();
  $("log").textContent = "";
  $("storyboardCard").style.display = "none";
  $("downloadRow").style.display = "none";
  appendLog("POST /api/pipeline/plan …");
  const res = await fetch("/api/pipeline/plan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...body, with_preview: true }),
  }).then((r) => r.json());
  state.projectId = res.project_id;
  appendLog(`project_id = ${res.project_id}`);
  trackJob(res);
  connectWs(res.project_id);
}

async function loadStoryboard(projectId) {
  const board = await fetch(`/api/pipeline/storyboard/${projectId}`).then((r) =>
    r.ok ? r.json() : null,
  );
  if (!board) return;
  state.storyboard = board;
  $("storyboardCard").style.display = "";
  $("storyboardMeta").textContent =
    `${board.title} — ${board.frames.length} scenes, about ` +
    `${Math.round(board.frames.reduce((t, f) => t + (f.estimated_ms || 0), 0) / 1000)}s`;
  $("storyboardList").innerHTML = board.frames
    .map(
      (f) => `
    <div class="sb-frame" data-scene="${f.scene_id}">
      <img src="${f.preview_url ? f.preview_url + "?v=" + Date.now() : ""}" alt="${escapeHtml(f.title)}"/>
      <div class="sb-body">
        <h4>${escapeHtml(f.scene_id)} · <input data-field="title" value="${escapeHtml(f.title)}"/></h4>
        <textarea data-field="visual_prompt" rows="2">${escapeHtml(f.visual_prompt)}</textarea>
        ${f.dialogue
          .map(
            (l) => `<div class="sb-line"><span>${escapeHtml(l.character_name || l.character_id)}</span>
                     <input data-line="${l.line_id}" value="${escapeHtml(l.text)}"/></div>`,
          )
          .join("")}
        <button class="secondary sb-save">Save scene</button>
      </div>
    </div>`,
    )
    .join("");
  document.querySelectorAll(".sb-save").forEach((btn) => {
    btn.addEventListener("click", () => saveScene(btn.closest(".sb-frame")));
  });
}

async function saveScene(frameEl) {
  const sceneId = frameEl.dataset.scene;
  const body = { dialogue: {} };
  frameEl.querySelectorAll("[data-field]").forEach((el) => {
    body[el.dataset.field] = el.value;
  });
  frameEl.querySelectorAll("[data-line]").forEach((el) => {
    body.dialogue[el.dataset.line] = el.value;
  });
  frameEl.classList.add("saving");
  appendLog(`saving ${sceneId} …`);
  const res = await fetch(
    `/api/pipeline/storyboard/${state.projectId}/${sceneId}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  frameEl.classList.remove("saving");
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    alert(`Save failed: ${err.detail || res.statusText}`);
    return;
  }
  appendLog(`${sceneId} updated`);
  loadStoryboard(state.projectId);
}

async function renderStoryboard() {
  if (!state.projectId) return;
  resetPhases();
  appendLog("POST /api/pipeline/render …");
  const res = await fetch(`/api/pipeline/render/${state.projectId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      with_bgm: $("bgm").checked,
      with_subtitles: $("subs").checked,
      subtitle_language: $("sub-lang").value,
      tts_engine: chosenEngine(),
    }),
  }).then((r) => r.json());
  trackJob(res);
  connectWs(res.project_id);
}

// ---- pipeline run ----------------------------------------------------------

async function startRun() {
  const prompt = $("prompt").value.trim();
  if (!prompt) {
    alert("Enter a prompt first.");
    return;
  }
  resetPhases();
  $("log").textContent = "";
  setRerunButtons(false);
  $("downloadRow").style.display = "none";
  $("metaPanel").textContent = "";

  const body = {
    prompt,
    target_duration_s: parseInt($("duration").value, 10),
    scene_count: parseInt($("scenes").value, 10),
    with_bgm: $("bgm").checked,
    with_subtitles: $("subs").checked,
    subtitle_language: $("sub-lang").value,
    tts_engine: chosenEngine(),
  };
  appendLog(`POST /api/pipeline/run …`);
  const res = await fetch("/api/pipeline/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    alert(`Could not start: ${err.detail || res.statusText}`);
    return;
  }
  const started = await res.json();
  state.projectId = started.project_id;
  appendLog(`project_id = ${started.project_id}`);
  trackJob(started);
  connectWs(started.project_id);
}

// ---- jobs ------------------------------------------------------------------

function trackJob(res) {
  if (!res || !res.job_id) return;
  state.jobId = res.job_id;
  const badge = $("jobBadge");
  badge.textContent = `job ${res.job_id} · ${res.status}`;
  badge.style.display = "";
  $("cancelJob").style.display = "";
  appendLog(`queued as ${res.job_id} — a worker picks it up`);
}

function jobFinished(label) {
  if (state.jobId) $("jobBadge").textContent = `job ${state.jobId} · ${label}`;
  $("cancelJob").style.display = "none";
}

async function cancelJob() {
  if (!state.jobId) return;
  $("cancelJob").disabled = true;
  try {
    const res = await fetch(`/api/jobs/${state.jobId}/cancel`, { method: "POST" })
      .then((r) => r.json());
    appendLog(`cancel requested — ${res.status}`);
    // A running job stops at its next step, so the badge waits for the event.
    if (res.status === "cancelled") jobFinished("cancelled");
  } finally {
    $("cancelJob").disabled = false;
  }
}

function connectWs(projectId) {
  if (state.ws) try { state.ws.close(); } catch (e) { /* ignore */ }
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const url = `${proto}//${location.host}/ws/progress/${projectId}`;
  appendLog(`WS connect ${url}`);
  const ws = new WebSocket(url);
  state.ws = ws;
  ws.onmessage = (msg) => {
    let env;
    try { env = JSON.parse(msg.data); } catch (e) { return; }
    if (env.type === "snapshot") return;
    if (env.type === "heartbeat") return;
    const ev = env.data;
    if (!ev) return;
    appendLog(`${ev.phase}: ${ev.message || ev.status} (${Math.round((ev.progress||0)*100)}%)`);
    setPhaseProgress(ev.phase, ev.status, ev.progress);
    if (ev.phase === "storyboard" && ev.status === "complete") {
      loadStoryboard(projectId);
      setRerunButtons(false);
    }
    if (ev.phase === "complete") {
      jobFinished("done");
      onPipelineComplete(projectId, ev.payload || {});
    } else if (ev.phase === "cancelled") {
      jobFinished("cancelled");
    } else if (ev.phase === "error") {
      jobFinished("failed");
      alert(`Pipeline failed: ${ev.message}`);
    }
  };
  ws.onclose = (ev) => {
    // 1008 is what the server sends when the session is gone or the project
    // isn't yours.
    if (ev.code === 1008) {
      appendLog("not signed in — reload to sign in again");
      location.reload();
      return;
    }
    appendLog("WS closed");
  };
}

async function attachSubtitles(projectId) {
  const player = $("player");
  player.querySelectorAll("track").forEach((t) => t.remove());
  let tracks = [];
  try {
    tracks = await fetch(`/api/pipeline/subtitles/${projectId}`).then((r) =>
      r.ok ? r.json() : [],
    );
  } catch (e) {
    return;
  }
  // Browsers ignore subtitles embedded in an MP4, so load the WebVTT sidecars.
  tracks
    .filter((t) => !t.burned_in)
    .forEach((t, i) => {
      const el = document.createElement("track");
      el.kind = "subtitles";
      el.label = t.language;
      el.srclang = t.code;
      el.src = t.url + "?v=" + Date.now();
      if (i === 0) el.default = true;
      player.appendChild(el);
    });
  if (tracks.length) {
    appendLog(`subtitles: ${tracks.map((t) => t.language + (t.burned_in ? " (burned in)" : "")).join(", ")}`);
  }
}

async function onPipelineComplete(projectId, payload) {
  appendLog("loading final state");
  const stateData = await fetch(`/api/pipeline/state/${projectId}`).then((r) => r.json());
  const video = stateData.video?.final_video_path;
  if (video) {
    const fileName = video.split(/[\\/]/).pop();
    const url = `/assets/${projectId}/${fileName}`;
    $("player").src = url;
    $("downloadVideo").href = url;
    $("downloadRow").style.display = "";
    attachSubtitles(projectId);
  }
  const meta = {
    title: stateData.script?.story?.title,
    genre: stateData.script?.story?.genre,
    themes: stateData.script?.story?.themes,
    scenes: stateData.script?.scenes?.length,
    characters: stateData.script?.characters?.characters?.map((c) => c.name),
    duration_ms: stateData.video?.duration_ms,
    version: stateData.version,
  };
  $("metaPanel").textContent = JSON.stringify(meta, null, 2);
  setRerunButtons(true);
  loadHistory(projectId);
  loadLibrary();
}

// ---- phase re-runs ---------------------------------------------------------

async function rerunPhase(phase) {
  if (!state.projectId) return;
  resetPhases();
  $("log").textContent = "";
  appendLog(`re-running phase: ${phase}`);
  const res = await fetch("/api/pipeline/rerun", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project_id: state.projectId, phase }),
  }).then((r) => r.json());
  connectWs(res.project_id);
}

// ---- edit agent ------------------------------------------------------------

async function classifyEdit() {
  const query = $("editQuery").value.trim();
  if (!query) return;
  const intent = await fetch("/api/edit/classify", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, project_id: state.projectId }),
  }).then((r) => r.json());
  state.intent = intent;
  $("intentPanel").style.display = "";
  $("intentPanel").textContent = JSON.stringify(intent, null, 2);
}

async function applyEdit() {
  const query = $("editQuery").value.trim();
  if (!query || !state.projectId) return;
  appendLog(`edit: ${query}`);
  const res = await fetch("/api/edit/apply", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project_id: state.projectId, query }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    alert(`Edit failed: ${err.detail || res.statusText}`);
    return;
  }
  const data = await res.json();
  state.intent = data.intent;
  $("intentPanel").style.display = "";
  $("intentPanel").textContent = JSON.stringify(data, null, 2);
  appendLog(`edit applied → version ${data.new_version}`);
  // Refresh preview + history.
  const stateData = await fetch(`/api/pipeline/state/${state.projectId}`).then((r) => r.json());
  const video = stateData.video?.final_video_path;
  if (video) {
    const fileName = video.split(/[\\/]/).pop();
    $("player").src = `/assets/${state.projectId}/${fileName}?v=${Date.now()}`;
  }
  loadHistory(state.projectId);
}

// ---- history + revert ------------------------------------------------------

async function loadHistory(projectId) {
  const rows = await fetch(`/api/history/${projectId}`).then((r) => r.json());
  if (!Array.isArray(rows) || rows.length === 0) {
    $("historyList").textContent = "No versions yet.";
    return;
  }
  $("historyList").innerHTML = rows.map((r) => `
    <div class="item">
      <div>
        <strong>v${r.version}</strong> — ${escapeHtml(r.description || "")}
        <div class="meta">${escapeHtml(r.created_at)} · ${r.asset_count || 0} assets</div>
      </div>
      <button class="secondary" data-revert="${r.version}">Revert</button>
    </div>
  `).join("");
  document.querySelectorAll("[data-revert]").forEach((btn) => {
    btn.addEventListener("click", () => revert(parseInt(btn.dataset.revert, 10)));
  });
}

async function revert(version) {
  if (!state.projectId) return;
  if (!confirm(`Revert to v${version}? Subsequent edits stay in history.`)) return;
  appendLog(`revert to v${version}`);
  const res = await fetch(
    `/api/history/${state.projectId}/revert/${version}`,
    { method: "POST" },
  );
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    alert(`Revert failed: ${err.detail || res.statusText}`);
    return;
  }
  const data = await res.json();
  appendLog(`reverted, new state version = ${data.new_state.version}`);
  // Refresh.
  const stateData = data.new_state;
  const video = stateData.video?.final_video_path;
  if (video) {
    const fileName = video.split(/[\\/]/).pop();
    $("player").src = `/assets/${state.projectId}/${fileName}?v=${Date.now()}`;
  }
  loadHistory(state.projectId);
}

// ---- subtitle languages ----------------------------------------------------

async function loadLanguages() {
  try {
    const langs = await fetch("/api/pipeline/languages").then((r) => r.json());
    const sel = $("sub-lang");
    sel.innerHTML = langs
      .map((l) => `<option value="${escapeHtml(l)}">${escapeHtml(l)}</option>`)
      .join("");
  } catch (e) {
    /* keep the English-only fallback option */
  }
}

// ---- voices ----------------------------------------------------------------

async function loadVoices() {
  try {
    state.voices = await fetch("/api/voices/").then((r) => r.json());
  } catch (e) {
    return; // the picker just stays on "Auto"
  }
  const auto = state.voices.engines.find((e) => e.is_default);
  $("voice-engine").innerHTML =
    `<option value="">Auto${auto ? ` (${escapeHtml(auto.label)})` : ""}</option>` +
    state.voices.engines
      .map(
        (e) =>
          `<option value="${escapeHtml(e.name)}"${e.available ? "" : " disabled"}>` +
          `${escapeHtml(e.label)}${e.available ? "" : " — unavailable"}</option>`,
      )
      .join("");
  fillVoiceSamples();
}

function currentEngine() {
  const name = $("voice-engine").value || state.voices.default || "";
  return state.voices.engines.find((e) => e.name === name) || null;
}

function fillVoiceSamples() {
  const engine = currentEngine();
  $("voice-sample").innerHTML = (engine ? engine.voices : [])
    .map((v) => `<option value="${escapeHtml(v.id)}">${escapeHtml(v.label)}</option>`)
    .join("");
  const note = $("voiceNote");
  if (!engine) note.textContent = "";
  else if (!engine.available)
    note.textContent = `${engine.label}: ${engine.unavailable_reason}`;
  else note.textContent = engine.summary;
}

async function previewVoice() {
  const engine = currentEngine();
  if (!engine) return;
  const btn = $("previewVoice");
  btn.disabled = true;
  try {
    const res = await fetch("/api/voices/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ engine: engine.name, voice: $("voice-sample").value }),
    });
    const body = await res.json();
    if (!res.ok) {
      $("voiceNote").textContent = body.detail || "Could not render a sample.";
      return;
    }
    if (body.fell_back) {
      $("voiceNote").textContent =
        `${engine.label} could not speak — you are hearing ${body.engine} instead.`;
    }
    const player = $("voicePlayer");
    player.src = `${body.url}?v=${Date.now()}`;
    player.play().catch(() => {});
  } finally {
    btn.disabled = false;
  }
}

// ---- provider badge --------------------------------------------------------

async function loadProviderBadge() {
  // Best-effort — not a critical path.
  $("providerBadge").textContent = "provider: ready";
}

// ---- chips -----------------------------------------------------------------

function bindChips() {
  document.querySelectorAll(".chip").forEach((btn) => {
    btn.addEventListener("click", () => {
      $("editQuery").value = btn.textContent;
    });
  });
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;",
    '"': "&quot;", "'": "&#39;",
  }[c]));
}

// ---- the films this account has made ---------------------------------------

async function loadLibrary() {
  let films = [];
  try {
    films = await fetch("/api/projects/").then((r) => (r.ok ? r.json() : []));
  } catch (e) {
    return;
  }
  const box = $("library");
  if (!films.length) {
    box.textContent = "Nothing yet — write a prompt above.";
    return;
  }
  box.innerHTML = films
    .map(
      (f) => `
      <button class="film${f.project_id === state.projectId ? " current" : ""}"
              data-pid="${escapeHtml(f.project_id)}"
              ${f.video_url ? "" : "data-unrendered=\"1\""}>
        <span class="film-title">${escapeHtml(f.title || "(untitled)")}</span>
        <span class="film-meta">${f.video_url ? "film" : "draft"} · v${f.version}</span>
      </button>`,
    )
    .join("");
  box.querySelectorAll(".film").forEach((btn) => {
    btn.addEventListener("click", () => openFilm(btn.dataset.pid));
  });
}

async function openFilm(projectId) {
  state.projectId = projectId;
  resetPhases();
  $("log").textContent = "";
  appendLog(`opening ${projectId}`);
  $("storyboardCard").style.display = "none";
  $("downloadRow").style.display = "none";

  // A rendered project fills the player; one still at the storyboard stage
  // opens its storyboard so it can be finished.
  const res = await fetch(`/api/pipeline/state/${projectId}`);
  if (!res.ok) {
    appendLog("that project is no longer available");
    return;
  }
  const stateData = await res.json();
  if (stateData.video?.final_video_path) {
    await onPipelineComplete(projectId, {});
  } else {
    await loadStoryboard(projectId);
    setRerunButtons(true);
    loadHistory(projectId);
  }
  loadLibrary();
}

// ---- sign-in gate ----------------------------------------------------------

const gate = {
  mode: "login",          // or "register"
  needsSetup: false,
};

async function authStatus() {
  try {
    return await fetch("/api/auth/status").then((r) => r.json());
  } catch (e) {
    return { authenticated: false, needs_setup: false, signups_allowed: false };
  }
}

function showGate(status) {
  gate.needsSetup = !!status.needs_setup;
  gate.mode = status.needs_setup ? "register" : "login";
  $("gate").hidden = false;
  paintGate(status);
}

function paintGate(status) {
  const registering = gate.mode === "register";
  $("gateTitle").textContent = gate.needsSetup
    ? "Create the first account"
    : registering ? "Create an account" : "Sign in";
  $("gateHint").textContent = gate.needsSetup
    ? "Nobody has signed up yet, so this account becomes the administrator."
    : registering
      ? `At least ${status.min_password_length || 10} characters.`
      : "";
  $("gateSubmit").textContent = registering ? "Create account" : "Sign in";
  $("gatePassword").setAttribute(
    "autocomplete", registering ? "new-password" : "current-password");

  const sw = $("gateSwitch");
  sw.innerHTML = "";
  if (!gate.needsSetup && status.signups_allowed) {
    const link = document.createElement("button");
    link.type = "button";
    link.className = "secondary small";
    link.textContent = registering ? "I already have an account" : "Create an account";
    link.addEventListener("click", () => {
      gate.mode = registering ? "login" : "register";
      $("gateError").hidden = true;
      paintGate(status);
    });
    sw.appendChild(link);
  }
}

async function submitGate(event) {
  event.preventDefault();
  const path = gate.mode === "register" ? "/api/auth/register" : "/api/auth/login";
  const error = $("gateError");
  error.hidden = true;
  $("gateSubmit").disabled = true;
  try {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: $("gateEmail").value.trim(),
        password: $("gatePassword").value,
      }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      error.textContent = body.detail || "That didn't work.";
      error.hidden = false;
      return;
    }
    $("gatePassword").value = "";
    $("gate").hidden = true;
    onSignedIn(body.user);
    startApp();
  } finally {
    $("gateSubmit").disabled = false;
  }
}

function onSignedIn(user) {
  $("whoami").textContent = user.role === "admin" ? `${user.email} (admin)` : user.email;
  $("signOut").hidden = false;
}

async function signOut() {
  await fetch("/api/auth/logout", { method: "POST" });
  // A reload is the simplest way to be sure nothing of the last account
  // is left on screen.
  location.reload();
}

// Anything the app loads from the API happens only once there is a session.
let appStarted = false;
function startApp() {
  if (appStarted) return;
  appStarted = true;
  loadLanguages();
  loadVoices();
  loadProviderBadge();
  loadLibrary();
}

// ---- wire up ---------------------------------------------------------------

document.addEventListener("DOMContentLoaded", () => {
  $("runBtn").addEventListener("click", startRun);
  $("planBtn").addEventListener("click", startPlan);
  $("renderBtn").addEventListener("click", renderStoryboard);
  $("rerunStory").addEventListener("click", () => rerunPhase("story"));
  $("rerunAudio").addEventListener("click", () => rerunPhase("audio"));
  $("rerunVideo").addEventListener("click", () => rerunPhase("video"));
  $("applyEdit").addEventListener("click", applyEdit);
  $("classifyEdit").addEventListener("click", classifyEdit);
  $("cancelJob").addEventListener("click", cancelJob);
  $("previewVoice").addEventListener("click", previewVoice);
  $("voice-engine").addEventListener("change", fillVoiceSamples);
  $("gateForm").addEventListener("submit", submitGate);
  $("signOut").addEventListener("click", signOut);
  bindChips();

  authStatus().then((status) => {
    if (status.authenticated) {
      onSignedIn(status.user);
      startApp();
    } else {
      showGate(status);
    }
  });
});
