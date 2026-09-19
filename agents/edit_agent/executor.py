"""Edit step executor — runs the targeted re-runs implied by an EditStep.

Edits reuse the same building blocks as a fresh pipeline run, so an edited
film is built exactly like a new one:

    audio edits  -> AudioAgent.render_line / retime / remix
    image edits  -> change shot-bank images or portraits in place
    every edit   -> VideoAgent.compose (re-cuts to the timeline, re-renders only
                    scenes whose shots changed, re-embeds subtitles)

Each handler returns the asset paths it created/modified; the EditAgent then
snapshots the whole state for undo.
"""
from __future__ import annotations
from typing import Dict, List

from agents.audio_agent import AudioAgent
from agents.video_agent import VideoAgent
from mcp.tool_executor import ToolExecutor
from shared.schemas.audio import VoiceConfig
from shared.schemas.pipeline import PipelineState
from shared.utils.files import project_dir
from shared.utils.logging import get_logger

from .planner import EditStep

log = get_logger("edit_executor")

MIN_SPEED, MAX_SPEED = 0.25, 4.0


class EditExecutor:
    """Carries out the re-runs implied by an edit step."""

    def __init__(self):
        self.tools = ToolExecutor()
        self.audio = AudioAgent()
        self.video = VideoAgent()

    # ---- entry -----------------------------------------------------------

    def execute(self, state: PipelineState, step: EditStep) -> List[str]:
        log.info("executing step %s (%s/%s) params=%s",
                 step.name, step.target, step.scope, step.params)
        handler = getattr(self, f"_step_{step.name}", None)
        if not handler:
            raise ValueError(f"no handler for step '{step.name}'")
        return handler(state, step) or []

    # ---- audio handlers --------------------------------------------------

    def _step_rerun_audio(self, state: PipelineState, step: EditStep) -> List[str]:
        voice_params = {k: v for k, v in (step.params or {}).items()
                        if k in ("tone", "volume", "voice")}
        if not state.audio or (step.scope == "global" and not voice_params):
            # Full regeneration (e.g. after the script changed).
            with_bgm = state.audio.bgm_enabled if state.audio else True
            self.audio.run(state, with_bgm=with_bgm)
            return list(state.phase2.artifact_paths)

        characters = {c.id: c for c in state.script.characters.characters}
        configs: Dict[str, VoiceConfig] = {v.character_id: v for v in state.audio.voice_configs}
        if step.scope.startswith("character:"):
            char_id = step.scope.split(":", 1)[1]
            if char_id not in configs:
                raise ValueError(f"unknown character '{char_id}'")
            self.audio.apply_voice_params(configs[char_id], voice_params, characters.get(char_id))
            targets = {s.line_id for s in state.audio.manifest.segments
                       if s.character_id == char_id}
            line_configs = configs
        elif step.scope.startswith("scene:"):
            scene_id = step.scope.split(":", 1)[1]
            targets = {s.line_id for s in state.audio.manifest.segments if s.scene_id == scene_id}
            if not targets:
                raise ValueError(f"scene '{scene_id}' has no dialogue")
            # Scene-scoped voice changes apply to this scene's lines only, so use
            # adjusted copies instead of changing each character's voice everywhere.
            line_configs = {}
            for cid, cfg in configs.items():
                copy = cfg.model_copy()
                self.audio.apply_voice_params(copy, voice_params, characters.get(cid))
                line_configs[cid] = copy
        else:  # global with voice params
            for cid, cfg in configs.items():
                self.audio.apply_voice_params(cfg, voice_params, characters.get(cid))
            targets = {s.line_id for s in state.audio.manifest.segments if s.kind == "dialogue"}
            line_configs = configs

        affected: List[str] = []
        for seg in state.audio.manifest.segments:
            if seg.kind != "dialogue" or seg.line_id not in targets:
                continue
            path, dur = self.audio.render_line(
                state.project_id, seg.scene_id, seg.line_id, seg.text or "",
                line_configs.get(seg.character_id), fallback_ms=seg.duration_ms,
            )
            seg.file_path, seg.duration_ms = path, dur
            affected.append(path)
        self._rebuild_audio(state)
        return affected + [state.audio.master_track]

    def _step_regenerate_bgm(self, state: PipelineState, step: EditStep) -> List[str]:
        if not state.audio or not state.script:
            return []
        mood = (step.params or {}).get("mood", "ambient")
        scene_ids = set(self._scene_ids_in_scope(state, step.scope))
        for scene in state.script.scenes:
            if scene.scene_id in scene_ids:
                scene.music_mood = mood
        state.audio.bgm_enabled = True
        self._rebuild_audio(state, retime=False)
        return [p for p in (state.audio.bgm_track, state.audio.master_track) if p]

    def _step_disable_bgm(self, state: PipelineState, step: EditStep) -> List[str]:
        if not state.audio:
            return []
        state.audio.bgm_enabled = False
        self._rebuild_audio(state, retime=False)
        return [state.audio.master_track]

    def _rebuild_audio(self, state: PipelineState, retime: bool = True) -> None:
        if retime:
            self.audio.retime(state)
        self.audio.remix(state)
        self.audio.serialize(state)

    # ---- video frame handlers -------------------------------------------

    def _step_apply_filter(self, state: PipelineState, step: EditStep) -> List[str]:
        """Filter every image a scene shows: its shot bank and its characters' portraits.

        Global scope edits the shared portraits; scene scope writes per-scene
        portrait copies so other scenes are untouched.
        """
        video = self._require_video(state)
        params = step.params or {}
        filt = params.get("filter") or params.get("filter_name") or params.get("aesthetic") \
            or "darker"
        scene_ids = self._scene_ids_in_scope(state, step.scope)
        global_scope = not step.scope.startswith("scene:")
        affected: List[str] = []

        for frame in video.frames:
            if frame.scene_id not in scene_ids:
                continue
            for img in frame.shot_bank or [frame.image_path]:
                affected.append(self._filter_image(img, img, filt))
            if global_scope:
                for char_id, path in frame.portrait_overrides.items():
                    affected.append(self._filter_image(path, path, filt))
            else:
                for char_id in self._speakers(state, frame.scene_id):
                    src = frame.portrait_overrides.get(char_id) or \
                        (video.portrait_for(char_id).image_path if video.portrait_for(char_id) else None)
                    if not src:
                        continue
                    dst = str(project_dir(state.project_id) / "video" / "portraits"
                              / f"{char_id}__{frame.scene_id}.png")
                    affected.append(self._filter_image(src, dst, filt))
                    frame.portrait_overrides[char_id] = dst
        if global_scope:
            for p in video.portraits:
                affected.append(self._filter_image(p.image_path, p.image_path, filt))
        return affected + self._recompose(state)

    def _step_regenerate_scene(self, state: PipelineState, step: EditStep) -> List[str]:
        """New shot-bank images (fresh seed per version) for the scenes in scope."""
        video = self._require_video(state)
        scene_ids = set(self._scene_ids_in_scope(state, step.scope))
        affected: List[str] = []
        for scene in state.script.scenes:
            if scene.scene_id not in scene_ids:
                continue
            frame = next((f for f in video.frames if f.scene_id == scene.scene_id), None)
            if frame is None:
                continue
            bank = self.video.generate_shot_bank(state.project_id, scene, video.width,
                                                 video.height, seed_salt=f"v{state.version + 1}")
            frame.shot_bank, frame.image_path = bank, bank[0]
            frame.t2v_clip = None  # was generated from the old establishing image
            affected.extend(bank)
        if not affected:
            raise ValueError(f"no scene matches scope '{step.scope}'")
        return affected + self._recompose(state)

    def _step_regenerate_portraits(self, state: PipelineState, step: EditStep) -> List[str]:
        """Change character design: regenerate portraits (all, or the one in scope)."""
        video = self._require_video(state)
        if step.scope.startswith("character:"):
            char_ids = {step.scope.split(":", 1)[1]}
        else:
            char_ids = {c.id for c in state.script.characters.characters if c.role != "narrator"}
        affected: List[str] = []
        for c in state.script.characters.characters:
            if c.id not in char_ids:
                continue
            new = self.video.generate_portrait(state.project_id, c, video.width, video.height,
                                               seed_salt=f"v{state.version + 1}")
            video.portraits = [new if p.character_id == c.id else p for p in video.portraits]
            if not video.portrait_for(c.id):
                video.portraits.append(new)
            for frame in video.frames:
                frame.portrait_overrides.pop(c.id, None)
            affected.append(new.image_path)
        if not affected:
            raise ValueError(f"no character matches scope '{step.scope}'")
        return affected + self._recompose(state)

    # ---- video handlers --------------------------------------------------

    def _step_recompose_video(self, state: PipelineState, step: EditStep) -> List[str]:
        video = self._require_video(state)
        if "subtitles" in (step.params or {}):
            video.has_subtitles = bool(step.params["subtitles"])
        return self._recompose(state)

    def _step_change_speed(self, state: PipelineState, step: EditStep) -> List[str]:
        video = self._require_video(state)
        factor = float((step.params or {}).get("factor", 1.5))
        if factor <= 0:
            raise ValueError(f"invalid speed factor {factor}")
        video.speed_factor = max(MIN_SPEED, min(MAX_SPEED, round(video.speed_factor * factor, 4)))
        return self._recompose(state)

    # ---- script handlers -------------------------------------------------

    def _step_regenerate_script(self, state: PipelineState, step: EditStep) -> List[str]:
        from agents.story_agent import StoryAgent
        params = step.params or {}
        if params.get("genre"):
            state.user_prompt = f"{state.user_prompt} (genre: {params['genre']})"
        StoryAgent().run(state, target_duration_s=state.script.story.target_duration_s,
                         scene_count=len(state.script.scenes))
        return list(state.phase1.artifact_paths)

    def _step_rerun_video(self, state: PipelineState, step: EditStep) -> List[str]:
        v = state.video
        self.video.run(
            state,
            with_subtitles=v.has_subtitles if v else True,
            subtitle_language=v.subtitle_language if v else "English",
            width=v.width if v else 1280, height=v.height if v else 720,
            fps=v.fps if v else 24,
            use_text_to_video=v.used_text_to_video if v else None,
            use_lip_sync=v.used_lip_sync if v else None,
            cinematic_post=v.cinematic_post if v else True,
        )
        return list(state.phase3.artifact_paths)

    # ---- helpers ---------------------------------------------------------

    def _recompose(self, state: PipelineState) -> List[str]:
        self.video.compose(state)
        return self.video.serialize(state)

    def _filter_image(self, src: str, dst: str, filt: str) -> str:
        res = self.tools.execute("vision.edit_image", in_path=src, out_path=dst, filters=[filt])
        if not res.success:
            raise ValueError(res.error)
        return res.data

    @staticmethod
    def _require_video(state: PipelineState):
        if not state.video or not state.script:
            raise ValueError("this edit needs a rendered video — run the pipeline first")
        return state.video

    @staticmethod
    def _speakers(state: PipelineState, scene_id: str) -> List[str]:
        scene = next((s for s in state.script.scenes if s.scene_id == scene_id), None)
        return sorted({ln.character_id for ln in scene.dialogue}) if scene else []

    @staticmethod
    def _scene_ids_in_scope(state: PipelineState, scope: str) -> List[str]:
        all_ids = [s.scene_id for s in state.script.scenes] if state.script else []
        if scope.startswith("scene:"):
            sid = scope.split(":", 1)[1]
            if sid not in all_ids:
                raise ValueError(f"unknown scene '{sid}' (have: {', '.join(all_ids)})")
            return [sid]
        return all_ids
