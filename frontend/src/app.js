/* Agentic Video Generator — single-page UI */

const $ = (id) => document.getElementById(id);

const state = {
  projectId: null,
  ws: null,
  intent: null,
  storyboard: null,
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
    }),
  }).then((r) => r.json());
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
  };
  appendLog(`POST /api/pipeline/run …`);
  const res = await fetch("/api/pipeline/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then((r) => r.json());
  state.projectId = res.project_id;
  appendLog(`project_id = ${res.project_id}`);
  connectWs(res.project_id);
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
      onPipelineComplete(projectId, ev.payload || {});
    } else if (ev.phase === "error") {
      alert(`Pipeline failed: ${ev.message}`);
    }
  };
  ws.onclose = () => appendLog("WS closed");
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
  bindChips();
  loadProviderBadge();
  loadLanguages();
});
