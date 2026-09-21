"""Phase 2 agent: ScriptOutput -> AudioOutput.

Per-line TTS with character-consistent voices, then the film's timeline
(shared/timeline.py), per-scene BGM sized to that timeline, and a master track
with every line placed at its timeline position. Emits the structured
AudioOutput and a flat timing_manifest.json.

The three steps are public so edits can reuse them:
    render_line()  -> (re)record one dialogue line
    retime()       -> recompute the timeline from current segment durations
    remix()        -> rebuild BGM + master from the timeline
"""
from __future__ import annotations
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mcp.tool_executor import ToolExecutor
from shared import providers
from shared.constants import PHASE_AUDIO
from shared.schemas.audio import (
    AudioOutput, AudioSegment, SceneTiming, TimingManifest, VoiceConfig,
)
from shared.schemas.pipeline import PipelineState
from shared.providers import ProviderSpec
from shared.schemas.story import Character, ScriptOutput
from shared.timeline import build_timeline, estimate_line_ms
from shared.utils.files import project_dir, write_json
from shared.utils.parallel import run_jobs
from shared.utils.logging import get_logger

log = get_logger("audio_agent")

DEFAULT_VOICE = "en-US-GuyNeural"
_SILENT = ProviderSpec(role="tts", provider="silent")
BGM_VOLUME = 0.18

# Alternate edge-tts voices per gender, used by "change voice" edits.
VOICE_POOL = {
    "female": ["en-US-AriaNeural", "en-US-JennyNeural", "en-GB-SoniaNeural",
               "en-AU-NatashaNeural"],
    "male": ["en-US-GuyNeural", "en-US-ChristopherNeural", "en-GB-RyanNeural",
             "en-AU-WilliamNeural"],
    "neutral": ["en-US-ChristopherNeural", "en-US-AriaNeural", "en-GB-RyanNeural",
                "en-US-JennyNeural"],
}

# Tone presets -> prosody (rate in wpm, pitch in Hz, volume multiplier).
TONE_PRESETS: Dict[str, Dict[str, Any]] = {
    "whispered": {"rate": 140, "pitch": -5, "volume": 0.7},
    "soft":      {"rate": 160, "pitch": -2, "volume": 0.75},
    "deep":      {"rate": 160, "pitch": -15, "volume": 1.0},
    "warm":      {"rate": 165, "pitch": -3, "volume": 1.0},
    "sad":       {"rate": 150, "pitch": -8, "volume": 0.85},
    "cheerful":  {"rate": 195, "pitch": 8, "volume": 1.05},
    "anxious":   {"rate": 200, "pitch": 5, "volume": 1.0},
    "angry":     {"rate": 190, "pitch": 4, "volume": 1.2},
    "loud":      {"rate": 175, "pitch": 0, "volume": 1.3},
}


class AudioAgent:
    def __init__(self):
        self.tools = ToolExecutor()

    # ---- public ----------------------------------------------------------

    def run(self, state: PipelineState, with_bgm: bool = True,
            tts_engine: Optional[str] = None) -> AudioOutput:
        if not state.script:
            raise ValueError("phase 2 requires state.script (run phase 1 first)")
        # Which voice engine serves this run comes from config/providers.yaml.
        tts_engine = tts_engine or (providers.active("tts") or _SILENT).provider
        log.info("phase 2 start (project=%s, engine=%s)", state.project_id, tts_engine)
        state.phase2.status = "running"
        state.phase2.started_at = datetime.utcnow().isoformat()

        try:
            voice_configs = self._build_voice_configs(state.script, default_engine=tts_engine)
            cfg_by_char = {v.character_id: v for v in voice_configs}

            lines = [(scene, line) for scene in state.script.scenes for line in scene.dialogue]
            rendered = run_jobs(
                [(self.render_line,
                  (state.project_id, scene.scene_id, line.line_id, line.text,
                   cfg_by_char.get(line.character_id), line.duration_ms))
                 for scene, line in lines],
                workers=providers.concurrency("tts"), label="tts lines",
            )
            segments: List[AudioSegment] = [
                AudioSegment(
                    segment_id=f"{scene.scene_id}_{line.line_id}",
                    scene_id=scene.scene_id, line_id=line.line_id,
                    character_id=line.character_id, file_path=path,
                    kind="dialogue", start_ms=0, end_ms=dur, duration_ms=dur,
                    text=line.text,
                )
                for (scene, line), (path, dur) in zip(lines, rendered)
            ]

            state.audio = AudioOutput(
                voice_configs=voice_configs,
                manifest=TimingManifest(project_id=state.project_id,
                                        total_duration_ms=0, segments=segments),
                bgm_enabled=with_bgm,
            )
            self.retime(state)
            self.remix(state)

            state.phase2.status = "complete"
            state.phase2.finished_at = datetime.utcnow().isoformat()
            state.phase2.artifact_paths = self._serialize(state.project_id, state.audio)
            log.info("phase 2 complete (%d dialogue segments, %.1fs timeline, master=%s)",
                     len(segments), state.audio.manifest.total_duration_ms / 1000,
                     state.audio.master_track)
            return state.audio
        except Exception as e:  # noqa: BLE001
            state.phase2.status = "failed"
            state.phase2.error = f"{type(e).__name__}: {e}"
            log.exception("phase 2 failed")
            raise

    def render_line(self, project_id: str, scene_id: str, line_id: str, text: str,
                    cfg: Optional[VoiceConfig], fallback_ms: int = 2500) -> Tuple[str, int]:
        """Record one dialogue line with its character's voice. Returns (path, duration_ms)."""
        audio_dir = project_dir(project_id) / "audio"
        audio_dir.mkdir(exist_ok=True, parents=True)
        out_file = audio_dir / f"{scene_id}_{line_id}.mp3"
        res = self.tools.execute(
            "audio.tts",
            text=text,
            out_path=str(out_file),
            engine=cfg.engine if cfg else "gtts",
            language=cfg.language if cfg else "en",
            tld=cfg.tld if cfg else "com",
            rate=cfg.rate if cfg else 175,
            pitch=cfg.pitch if cfg else 0,
            volume=cfg.volume if cfg else 1.0,
            voice=(cfg.voice_id if cfg and cfg.voice_id else DEFAULT_VOICE),
        )
        if res.success:
            rendered = Path(res.data)
        else:
            log.warning("tts failed for %s: %s — using silent placeholder", line_id, res.error)
            rendered = audio_dir / f"{scene_id}_{line_id}.wav"
            self.tools.execute("audio.tts", text=text, out_path=str(rendered), engine="silent")
        duration = self._probe_duration_ms(rendered) or fallback_ms or estimate_line_ms(text)
        return str(rendered), duration

    def retime(self, state: PipelineState) -> TimingManifest:
        """Lay every segment out on the film timeline (script order, real durations)."""
        manifest = state.audio.manifest
        seg_by_line = {s.line_id: s for s in manifest.segments if s.kind == "dialogue"}
        plan = []
        for scene in state.script.scenes:
            lines = [(ln.line_id, seg_by_line[ln.line_id].duration_ms)
                     for ln in scene.dialogue if ln.line_id in seg_by_line]
            plan.append((scene.scene_id, lines, scene.duration_ms))
        scene_slots, line_slots, total_ms = build_timeline(plan)

        ordered: List[AudioSegment] = []
        for slot in line_slots:
            seg = seg_by_line[slot.line_id]
            seg.start_ms, seg.end_ms = slot.start_ms, slot.end_ms
            ordered.append(seg)
        manifest.segments = ordered + [s for s in manifest.segments if s.kind != "dialogue"]
        manifest.scenes = [SceneTiming(scene_id=s.scene_id, start_ms=s.start_ms, end_ms=s.end_ms)
                           for s in scene_slots]
        manifest.total_duration_ms = total_ms
        return manifest

    def remix(self, state: PipelineState) -> str:
        """Rebuild the BGM bed (if enabled) and the master track from the timeline."""
        audio = state.audio
        audio.bgm_track = self._render_bgm(state) if audio.bgm_enabled else None
        out = project_dir(state.project_id) / "audio" / "master.wav"
        placements = [{"path": s.file_path, "start_ms": s.start_ms}
                      for s in audio.manifest.segments if s.kind == "dialogue"]
        merge = self.tools.execute(
            "audio.merge",
            out_path=str(out),
            placements=placements,
            total_ms=audio.manifest.total_duration_ms,
            bgm=audio.bgm_track,
            bgm_volume=BGM_VOLUME,
        )
        if not merge.success:
            raise RuntimeError(f"master mix failed: {merge.error}")
        audio.master_track = merge.data
        return merge.data

    # ---- voice configs ---------------------------------------------------

    def _build_voice_configs(self, script: ScriptOutput, default_engine: str) -> List[VoiceConfig]:
        out: List[VoiceConfig] = []
        for c in script.characters.characters:
            out.append(VoiceConfig(
                character_id=c.id,
                engine=default_engine,
                language="en",
                voice_id=self._edge_voice_for(c),
                tld=self._tld_for(c),
                rate=self._rate_for(c),
                tone=c.voice_style,
            ))
        return out

    @staticmethod
    def apply_voice_params(cfg: VoiceConfig, params: Dict[str, Any],
                           character: Optional[Character] = None) -> None:
        """Apply an edit's voice parameters (tone / volume / voice) to a config."""
        tone = params.get("tone")
        if tone:
            preset = TONE_PRESETS.get(tone, {})
            cfg.tone = tone
            cfg.rate = preset.get("rate", cfg.rate)
            cfg.pitch = preset.get("pitch", cfg.pitch)
            cfg.volume = preset.get("volume", cfg.volume)
            if tone == "deep":
                cfg.tld = "co.uk"  # gTTS fallback has no pitch control
        volume = params.get("volume")
        if volume is not None:
            cfg.volume = max(0.0, min(2.0, round(cfg.volume * float(volume), 3)))
        voice = params.get("voice")
        if voice:
            if voice == "alternate":
                gender = character.voice_gender if character else "neutral"
                pool = VOICE_POOL.get(gender, VOICE_POOL["neutral"])
                current = cfg.voice_id or ""
                idx = pool.index(current) if current in pool else -1
                cfg.voice_id = pool[(idx + 1) % len(pool)]
            else:
                cfg.voice_id = str(voice)

    @staticmethod
    def _tld_for(c: Character) -> str:
        # Different TLDs give noticeably different gTTS accents for the same lang.
        if c.voice_gender == "female":
            return "co.uk"
        if c.voice_age == "elderly":
            return "co.in"
        if c.role == "narrator":
            return "com"
        return "com.au"

    @staticmethod
    def _edge_voice_for(c: Character) -> str:
        if c.role == "narrator":
            return "en-US-ChristopherNeural"
        if c.voice_age == "child" or "energetic" in c.voice_style.lower():
            return "en-US-AnaNeural"
        if c.voice_age == "elderly":
            return "en-GB-RyanNeural"
        if c.voice_gender == "female":
            return "en-US-AriaNeural"
        return "en-US-GuyNeural"

    @staticmethod
    def _rate_for(c: Character) -> int:
        if "whisper" in c.voice_style.lower():
            return 140
        if "energetic" in c.voice_style.lower() or c.voice_age == "child":
            return 200
        return 175

    # ---- BGM -------------------------------------------------------------

    def _render_bgm(self, state: PipelineState) -> Optional[str]:
        """One mood bed per scene, sized to the scene's timeline slot, joined end to end."""
        bgm_dir = project_dir(state.project_id) / "audio" / "bgm"
        bgm_dir.mkdir(exist_ok=True, parents=True)
        mood_by_scene = {s.scene_id: s.music_mood for s in state.script.scenes}
        per_scene_paths: List[str] = []
        for timing in state.audio.manifest.scenes:
            out_file = bgm_dir / f"{timing.scene_id}_bgm.wav"
            res = self.tools.execute(
                "audio.bgm",
                mood=mood_by_scene.get(timing.scene_id, "ambient"),
                duration_ms=timing.duration_ms,
                out_path=str(out_file),
            )
            if res.success:
                per_scene_paths.append(res.data)
            else:
                log.warning("bgm failed for %s: %s", timing.scene_id, res.error)
        if not per_scene_paths:
            return None
        merged = bgm_dir / "bgm_full.wav"
        merge = self.tools.execute("audio.merge", segments=per_scene_paths,
                                   out_path=str(merged))
        return merge.data if merge.success else None

    # ---- helpers ---------------------------------------------------------

    @staticmethod
    def _probe_duration_ms(path: Path) -> Optional[int]:
        if not path.exists():
            return None
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                capture_output=True, text=True, check=True,
            )
            return int(float(r.stdout.strip()) * 1000)
        except Exception:  # noqa: BLE001
            return None

    # ---- serialization ---------------------------------------------------

    def _serialize(self, project_id: str, output: AudioOutput) -> List[str]:
        proj = project_dir(project_id)
        manifest_path = proj / "timing_manifest.json"
        write_json(manifest_path, output.manifest.model_dump(mode="json"))
        audio_summary = proj / "audio_summary.json"
        write_json(audio_summary, {
            "project_id": project_id,
            "phase": PHASE_AUDIO,
            "status": "complete",
            "voice_configs": [v.model_dump(mode="json") for v in output.voice_configs],
            "segment_count": len(output.manifest.segments),
            "total_duration_ms": output.manifest.total_duration_ms,
            "bgm_track": output.bgm_track,
            "master_track": output.master_track,
        })
        artifacts = [str(manifest_path), str(audio_summary)]
        if output.bgm_track:
            artifacts.append(output.bgm_track)
        if output.master_track:
            artifacts.append(output.master_track)
        artifacts.extend(s.file_path for s in output.manifest.segments)
        return artifacts

    def serialize(self, state: PipelineState) -> List[str]:
        """Re-write the audio artifacts after an edit and refresh artifact paths."""
        state.phase2.artifact_paths = self._serialize(state.project_id, state.audio)
        return state.phase2.artifact_paths
