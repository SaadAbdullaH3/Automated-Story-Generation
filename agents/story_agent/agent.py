"""Phase 1 agent: prompt -> validated ScriptOutput.

Architecture follows the LangGraph-style pipeline shown in the spec diagram:
    [Story agent] -> [Character agent] -> [Script agent]
with retries and an error handler. We implement it as a small in-process graph
rather than pulling in LangGraph as a hard dependency, so the project runs
without extra installs. The state shape and retry behavior match what a
LangGraph implementation would look like.
"""
from __future__ import annotations
from datetime import datetime

from pydantic import ValidationError

from mcp.tools.llm_tools.llm_client import get_llm_client
from shared.constants import PHASE_STORY
from shared.schemas.pipeline import PipelineState
from shared.schemas.story import Character, ScriptOutput
from shared.timeline import SCENE_PREROLL_MS, SCENE_TAIL_MS, WORDS_PER_SECOND
from shared.utils.files import project_dir, write_json
from shared.utils.logging import get_logger

from .planner import template_script

log = get_logger("story_agent")


SYSTEM_PROMPT = """You are a master screenwriter and story architect.
Your job is to transform a user's one-line prompt into a complete short film script
with characters, scenes, and dialogue. You must respond with a single valid JSON
object that matches the requested schema EXACTLY. No prose, no markdown fences."""


SCRIPT_USER_PROMPT = """User prompt: "{prompt}"

Target duration: {duration_s} seconds (about {scene_count} scenes of equal length).

Generate a complete script JSON object with this exact structure:
{{
  "story": {{
    "project_id": "{project_id}",
    "title": "...",
    "logline": "one sentence pitch",
    "synopsis": "one paragraph summary",
    "genre": "...",
    "themes": ["...", "..."],
    "arc": "three-act",
    "target_duration_s": {duration_s}
  }},
  "characters": {{
    "characters": [
      {{
        "id": "char_narrator",
        "name": "Narrator",
        "role": "narrator",
        "description": "one sentence personality",
        "visual_description": "appearance details for image generation, very specific",
        "voice_style": "warm/whispered/deep/cheerful/anxious/etc",
        "voice_gender": "male|female|neutral",
        "voice_age": "child|young|adult|elderly"
      }}
    ]
  }},
  "scenes": [
    {{
      "scene_id": "scene_1",
      "index": 0,
      "title": "...",
      "setting": "where + when",
      "tone": "neutral|tense|joyful|melancholic",
      "visual_prompt": "detailed image-gen prompt with subject, setting, lighting, camera angle",
      "camera": "wide shot / medium / close-up / aerial",
      "duration_ms": {scene_duration_ms},
      "dialogue": [
        {{
          "line_id": "scene_1_l1",
          "character_id": "char_narrator",
          "text": "spoken line",
          "emotion": "reflective",
          "duration_ms": 4000
        }}
      ],
      "music_mood": "ambient|tense|epic|mysterious|joyful|sad|ethereal",
      "transition_in": "fade|cut|slide|zoom"
    }}
  ]
}}

Rules:
- Include 3-4 characters total, each with a consistent visual + voice identity.
- Generate {scene_count} total scenes forming a clear arc (intro -> rising -> climax -> resolution).
- Every dialogue line's character_id MUST match a character.id from the roster.
- Visual prompts should be self-contained and image-gen friendly.
- Total of all scene duration_ms should be roughly {total_ms}.
- Keep ALL spoken dialogue together to about {word_budget} words (people speak ~2.6 words
  per second, and each scene also needs ~2 seconds without speech). This is what makes
  the film land near {duration_s} seconds, so respect it.
- Make the dialogue feel natural and specific to the genre.
- The protagonist should appear in every scene; supporting characters can vary.
"""


class StoryAgent:
    def __init__(self):
        self.llm = get_llm_client()

    # ---- public ----------------------------------------------------------

    def run(self, state: PipelineState, target_duration_s: int = 45,
            scene_count: int = 4) -> ScriptOutput:
        log.info("phase 1 start (provider=%s, project=%s)",
                 self.llm.provider, state.project_id)
        state.phase1.status = "running"
        state.phase1.started_at = datetime.utcnow().isoformat()

        try:
            script = self._generate(state.project_id, state.user_prompt,
                                    target_duration_s, scene_count)
            self._validate(script)
            artifacts = self._serialize(state.project_id, script)
            state.script = script
            state.phase1.status = "complete"
            state.phase1.finished_at = datetime.utcnow().isoformat()
            state.phase1.artifact_paths = artifacts
            log.info("phase 1 complete (%d characters, %d scenes)",
                     len(script.characters.characters), len(script.scenes))
            return script
        except Exception as e:  # noqa: BLE001
            state.phase1.status = "failed"
            state.phase1.error = f"{type(e).__name__}: {e}"
            log.exception("phase 1 failed")
            raise

    # ---- generation ------------------------------------------------------

    def _generate(self, project_id: str, prompt: str,
                  duration_s: int, scene_count: int) -> ScriptOutput:
        # Mock provider -> deterministic template fallback.
        if self.llm.provider == "mock":
            log.info("using template-based fallback (no LLM provider configured)")
            return template_script(project_id, prompt, target_duration_s=duration_s, scene_count=scene_count)

        scene_duration_ms = (duration_s * 1000) // scene_count
        total_ms = scene_duration_ms * scene_count
        speech_s = duration_s - scene_count * (SCENE_PREROLL_MS + SCENE_TAIL_MS) / 1000
        word_budget = max(scene_count * 6, int(speech_s * WORDS_PER_SECOND))
        user_prompt = SCRIPT_USER_PROMPT.format(
            prompt=prompt,
            duration_s=duration_s,
            scene_count=scene_count,
            scene_duration_ms=scene_duration_ms,
            total_ms=total_ms,
            word_budget=word_budget,
            project_id=project_id,
        )
        try:
            return self.llm.generate_structured(
                prompt=user_prompt,
                schema=ScriptOutput,
                system=SYSTEM_PROMPT,
                temperature=0.8,
                max_tokens=4000,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("structured LLM gen failed (%s) — using template fallback", e)
            return template_script(project_id, prompt, target_duration_s=duration_s, scene_count=scene_count)

    # ---- validation (check_consistency / estimate_duration tools) -------

    def _validate(self, script: ScriptOutput) -> None:
        char_ids = {c.id for c in script.characters.characters}
        for scene in script.scenes:
            for line in scene.dialogue:
                if line.character_id not in char_ids:
                    raise ValueError(
                        f"unknown character_id {line.character_id} "
                        f"in {scene.scene_id}/{line.line_id}"
                    )
        if not script.scenes:
            raise ValueError("scenes must not be empty")

    # ---- serialization (matches diagram artifacts) -----------------------

    def _serialize(self, project_id: str, script: ScriptOutput) -> list[str]:
        proj = project_dir(project_id)
        story_path = proj / "story.json"
        chars_path = proj / "characters.json"
        script_path = proj / "script.json"
        audio_handoff = proj / "phase2_audio_handoff.json"
        video_handoff = proj / "phase3_video_handoff.json"
        summary_path = proj / "summary.json"

        write_json(story_path, script.story.model_dump(mode="json"))
        write_json(chars_path, script.characters.model_dump(mode="json"))
        write_json(script_path, [s.model_dump(mode="json") for s in script.scenes])

        # Phase 2 hand-off: per-character voice configs + per-line text.
        audio_handoff_data = {
            "project_id": project_id,
            "voice_configs": [
                {
                    "character_id": c.id,
                    "engine": "gtts",
                    "language": "en",
                    "tld": _tld_for(c),
                    "voice_style": c.voice_style,
                    "voice_gender": c.voice_gender,
                    "voice_age": c.voice_age,
                } for c in script.characters.characters
            ],
            "segments": [
                {
                    "scene_id": s.scene_id,
                    "line_id": ln.line_id,
                    "character_id": ln.character_id,
                    "text": ln.text,
                    "emotion": ln.emotion,
                    "duration_ms": ln.duration_ms,
                } for s in script.scenes for ln in s.dialogue
            ],
            "music_moods": [
                {"scene_id": s.scene_id, "mood": s.music_mood, "duration_ms": s.duration_ms}
                for s in script.scenes
            ],
        }
        write_json(audio_handoff, audio_handoff_data)

        # Phase 3 hand-off: per-scene visual prompts + camera + transitions.
        video_handoff_data = {
            "project_id": project_id,
            "scenes": [
                {
                    "scene_id": s.scene_id,
                    "index": s.index,
                    "visual_prompt": s.visual_prompt,
                    "camera": s.camera,
                    "duration_ms": s.duration_ms,
                    "transition_in": s.transition_in,
                    "subtitles": [
                        {"line_id": ln.line_id, "text": ln.text,
                         "duration_ms": ln.duration_ms}
                        for ln in s.dialogue
                    ],
                } for s in script.scenes
            ],
        }
        write_json(video_handoff, video_handoff_data)

        # Run summary.
        write_json(summary_path, {
            "project_id": project_id,
            "phase": PHASE_STORY,
            "status": "complete",
            "title": script.story.title,
            "scene_count": len(script.scenes),
            "character_count": len(script.characters.characters),
            "total_duration_ms": script.total_duration_ms(),
            "artifacts": [str(story_path), str(chars_path), str(script_path),
                          str(audio_handoff), str(video_handoff)],
        })
        return [str(story_path), str(chars_path), str(script_path),
                str(audio_handoff), str(video_handoff), str(summary_path)]


def _tld_for(c: Character) -> str:
    """Pick a gTTS regional accent that varies voices across the cast."""
    if c.voice_gender == "female":
        return "co.uk"
    if c.voice_age == "elderly":
        return "co.in"
    if c.role == "narrator":
        return "com"
    return "com.au"
