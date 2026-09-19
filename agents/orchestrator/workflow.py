"""High-level pipeline orchestrator.

Composes Phase 1 -> 2 -> 3 with progress events suitable for SSE/WebSocket
streaming, and snapshots the final state via StateManager.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Iterator, List, Optional

from agents.audio_agent import AudioAgent
from agents.story_agent import StoryAgent
from agents.video_agent import VideoAgent
from shared.schemas.pipeline import PipelineState
from shared.utils.ids import new_project_id
from shared.utils.logging import get_logger
from state_manager.snapshot import referenced_files
from state_manager.state_manager import StateManager

from .graph import PipelineGraph
from .state import RunContext

log = get_logger("orchestrator")


@dataclass
class ProgressEvent:
    """Streamed to clients to drive the per-phase progress UI."""
    phase: str               # "story" | "audio" | "video" | "complete" | "error"
    status: str              # "started" | "complete" | "failed"
    message: str = ""
    progress: float = 0.0    # 0.0 - 1.0
    project_id: str = ""
    payload: dict = field(default_factory=dict)


class PipelineOrchestrator:
    def __init__(self, state_manager: Optional[StateManager] = None):
        self.sm = state_manager or StateManager()
        self.story = StoryAgent()
        self.audio = AudioAgent()
        self.video = VideoAgent()

    # ---- public ----------------------------------------------------------

    def run_full(
        self,
        prompt: str,
        on_event: Optional[Callable[[ProgressEvent], None]] = None,
        target_duration_s: int = 45,
        scene_count: int = 4,
        with_bgm: bool = True,
        with_subtitles: bool = True,
        subtitle_language: str = "English",
        project_id: Optional[str] = None,
        use_text_to_video: Optional[bool] = None,
        use_lip_sync: Optional[bool] = None,
    ) -> PipelineState:
        project_id = project_id or new_project_id()
        state = PipelineState(project_id=project_id, user_prompt=prompt)
        ctx = RunContext(
            state=state,
            target_duration_s=target_duration_s,
            scene_count=scene_count,
            with_bgm=with_bgm,
            with_subtitles=with_subtitles,
            subtitle_language=subtitle_language,
            use_text_to_video=use_text_to_video,
            use_lip_sync=use_lip_sync,
        )

        graph = self._build_graph()
        emit = on_event or (lambda _e: None)

        emit(ProgressEvent(phase="story", status="started", project_id=project_id,
                           message="Generating story, characters, and scenes",
                           progress=0.05))
        try:
            graph.run(ctx,
                      on_node=lambda name, _c: emit(self._node_event(name, project_id, done=False)),
                      on_node_done=lambda name, _c: emit(self._node_event(name, project_id, done=True)))
        except Exception as e:  # noqa: BLE001
            emit(ProgressEvent(phase="error", status="failed", project_id=project_id,
                               message=str(e), progress=1.0))
            raise

        # Final snapshot.
        version = self.sm.snapshot(
            state,
            asset_paths=self._collect_assets(state),
            description="initial pipeline run",
        )
        emit(ProgressEvent(phase="complete", status="complete", project_id=project_id,
                           message=f"Pipeline finished — version {version.version}",
                           progress=1.0,
                           payload={"version": version.version,
                                    "video_path": state.video.final_video_path
                                    if state.video else None}))
        return state

    # ---- generator variant for SSE/WebSocket streaming ------------------

    def stream_full(self, prompt: str, **kwargs) -> Iterator[ProgressEvent]:
        events: List[ProgressEvent] = []

        def collector(ev: ProgressEvent) -> None:
            events.append(ev)

        # We can't yield from inside on_event because run_full is synchronous;
        # the FastAPI WebSocket route will call run_full with its own callback.
        self.run_full(prompt, on_event=collector, **kwargs)
        for ev in events:
            yield ev

    def re_run_phase(self, project_id: str, phase: str,
                     on_event: Optional[Callable[[ProgressEvent], None]] = None) -> PipelineState:
        state = self.sm.latest(project_id)
        if not state:
            raise ValueError(f"no project state for {project_id}")
        if phase not in ("story", "audio", "video"):
            raise ValueError(f"unknown phase '{phase}'")
        emit = on_event or (lambda _e: None)

        # Re-runs keep the project's existing settings.
        v, a = state.video, state.audio
        with_bgm = a.bgm_enabled if a else True
        video_kwargs = dict(
            with_subtitles=v.has_subtitles if v else True,
            subtitle_language=v.subtitle_language if v else "English",
            width=v.width if v else 1280, height=v.height if v else 720,
            fps=v.fps if v else 24,
            use_text_to_video=v.used_text_to_video if v else None,
            use_lip_sync=v.used_lip_sync if v else None,
            cinematic_post=v.cinematic_post if v else True,
        )
        steps = ["story", "audio", "video"][["story", "audio", "video"].index(phase):]
        for i, step in enumerate(steps):
            emit(ProgressEvent(phase=step, status="started", project_id=project_id,
                               message=f"Re-running {step}", progress=0.1 + 0.8 * i / len(steps)))
            if step == "story":
                self.story.run(state, target_duration_s=state.script.story.target_duration_s
                               if state.script else 45,
                               scene_count=len(state.script.scenes) if state.script else 4)
            elif step == "audio":
                self.audio.run(state, with_bgm=with_bgm)
            else:
                self.video.run(state, **video_kwargs)
            emit(ProgressEvent(phase=step, status="complete", project_id=project_id,
                               message=f"{step.capitalize()} regenerated",
                               progress=0.1 + 0.8 * (i + 1) / len(steps)))
        version = self.sm.snapshot(
            state,
            asset_paths=self._collect_assets(state),
            description=f"re-run phase: {phase}",
        )
        emit(ProgressEvent(phase="complete", status="complete", project_id=project_id,
                           message=f"Phase {phase} re-run, version {version.version}",
                           progress=1.0,
                           payload={"version": version.version}))
        return state

    # ---- graph wiring ----------------------------------------------------

    def _build_graph(self) -> PipelineGraph:
        g = PipelineGraph()
        g.add("phase1_story",
              lambda c: self.story.run(c.state, target_duration_s=c.target_duration_s,
                                       scene_count=c.scene_count),
              next_=["phase2_audio"], entry=True)
        g.add("phase2_audio",
              lambda c: self.audio.run(c.state, with_bgm=c.with_bgm),
              next_=["phase3_video"])
        g.add("phase3_video",
              lambda c: self.video.run(
                  c.state, with_subtitles=c.with_subtitles,
                  subtitle_language=c.subtitle_language,
                  width=c.width, height=c.height, fps=c.fps,
                  use_text_to_video=c.use_text_to_video,
                  use_lip_sync=c.use_lip_sync,
              ),
              next_=[])
        return g

    @staticmethod
    def _node_event(name: str, project_id: str, done: bool) -> ProgressEvent:
        # (phase, started message, started progress, done message, done progress)
        mapping = {
            "phase1_story": ("story", "Generating story + characters", 0.10,
                             "Story, characters and scenes ready", 0.30),
            "phase2_audio": ("audio", "Synthesizing voices + BGM", 0.35,
                             "Voices, music and timeline ready", 0.55),
            "phase3_video": ("video", "Generating images + composing video", 0.60,
                             "Video composed", 0.95),
        }
        phase, start_msg, start_p, done_msg, done_p = mapping.get(
            name, (name, name, 0.5, name, 0.5))
        return ProgressEvent(phase=phase, status="complete" if done else "started",
                             project_id=project_id,
                             message=done_msg if done else start_msg,
                             progress=done_p if done else start_p)

    @staticmethod
    def _collect_assets(state: PipelineState) -> List[str]:
        return referenced_files(state)
