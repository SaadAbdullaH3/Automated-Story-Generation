"""M8 — voice edits compose instead of overwriting each other.

A scene-scoped voice edit used to render that scene's lines from throwaway
copies of each character's voice, and nothing kept them. The next edit that
touched those lines — "make everyone louder", a change to one character, or
"regenerate the audio" — rebuilt them from the character's own voice, and the
scene's change silently disappeared.

Every test here goes through EditAgent.edit on a fully rendered film and reads
back the TTS calls the edit actually made.
"""
from __future__ import annotations
from pathlib import Path

import pytest

from agents.audio_agent import AudioAgent
from agents.edit_agent import EditAgent
from agents.edit_agent.executor import EditExecutor
from agents.edit_agent.intent_classifier import classify
from agents.edit_agent.planner import EditStep
from shared.schemas.edit import EditCommand
from tests.conftest import silence_tts

# 40 s over 4 scenes: Aria speaks in scenes 2 and 3, Kai in 2, 3 and 4, the
# narrator in 1 and 4 — enough to tell "this scene" from "this character".
FILM = dict(duration_s=40, scenes=4)
WHISPER = dict(rate=140, pitch=-5, volume=0.7)


def _agent(sm):
    agent = EditAgent(sm)
    return agent, silence_tts(agent.executor.audio.tools)


def _lines(state):
    """line file stem -> (scene_id, character_id), to read the TTS calls back."""
    return {f"{s.scene_id}_{s.line_id}": (s.scene_id, s.character_id)
            for s in state.audio.manifest.segments if s.kind == "dialogue"}


def _recorded(state, calls):
    """[(scene_id, character_id, call kwargs)] for every line an edit re-recorded."""
    lines = _lines(state)
    return [(*lines[Path(c["out_path"]).stem], c) for c in calls]


def _edit(agent, state, query):
    result = agent.edit(EditCommand(project_id=state.project_id, query=query))
    assert result.success, result.error
    return result


def test_a_scene_voice_change_survives_a_later_global_edit(small_project):
    state, sm = small_project(**FILM)
    agent, calls = _agent(sm)

    r = _edit(agent, state, "make the voices in scene 2 whispered")
    assert r.intent.scope == "scene:scene_2"
    assert {scene for scene, _, _ in _recorded(state, calls)} == {"scene_2"}

    calls.clear()
    _edit(agent, state, "make everyone louder")
    recorded = _recorded(state, calls)
    assert {scene for scene, _, _ in recorded} == {"scene_1", "scene_2", "scene_3", "scene_4"}
    for scene, char, call in recorded:
        if scene == "scene_2":
            # Still whispering — and louder, because that came after.
            assert (call["rate"], call["pitch"]) == (WHISPER["rate"], WHISPER["pitch"]), char
            assert call["volume"] == pytest.approx(WHISPER["volume"] * 1.3)
        else:
            assert call["rate"] != WHISPER["rate"], (scene, char)
            assert call["volume"] == pytest.approx(1.3)


def test_a_character_edit_keeps_that_characters_scene_only_voice(small_project):
    state, sm = small_project(**FILM)
    agent, calls = _agent(sm)
    aria = next(v.voice_id for v in state.audio.voice_configs
                if v.character_id == "char_protagonist")

    _edit(agent, state, "change the voice in scene 2")
    scene2_voice = {char: c["voice"] for _, char, c in _recorded(state, calls)}
    assert scene2_voice["char_protagonist"] != aria

    calls.clear()
    r = _edit(agent, state, "change aria's voice tone to whispered")
    assert r.intent.scope == "character:char_protagonist"
    recorded = _recorded(state, calls)
    assert {(scene, char) for scene, char, _ in recorded} == {
        ("scene_2", "char_protagonist"), ("scene_3", "char_protagonist")}
    for scene, _, call in recorded:
        # Whispering everywhere, in the voice each scene had.
        assert call["rate"] == WHISPER["rate"]
        assert call["voice"] == (scene2_voice["char_protagonist"] if scene == "scene_2" else aria)


def test_the_same_scene_edit_twice_moves_on_instead_of_repeating(small_project):
    """"Change the voice in scene 2" twice must land on a third voice, not the
    second one again — which is what re-deriving from the base voice gave."""
    state, sm = small_project(**FILM)
    agent, calls = _agent(sm)
    voices = []
    for _ in range(2):
        calls.clear()
        _edit(agent, state, "change the voice in scene 2")
        voices.append({char: c["voice"] for _, char, c in _recorded(state, calls)})
    assert voices[0]["char_protagonist"] != voices[1]["char_protagonist"]


def test_regenerating_the_audio_keeps_every_voice_and_the_engine(small_project):
    state, sm = small_project(**FILM)
    # Recorded with Kokoro, which this test machine's tts chain would not
    # pick on its own: a regeneration that forgot the engine shows up here.
    latest = sm.latest(state.project_id)
    for cfg in latest.audio.voice_configs:
        cfg.engine, cfg.voice_id = "kokoro", "af_heart"
    sm.snapshot(latest, asset_paths=[], description="recorded with kokoro")
    agent, calls = _agent(sm)

    _edit(agent, state, "make the voices in scene 2 whispered")
    calls.clear()
    r = _edit(agent, state, "regenerate the audio")
    assert r.intent.intent == "regenerate_audio"
    recorded = _recorded(state, calls)
    assert len(recorded) == len(_lines(state))
    assert {c["engine"] for _, _, c in recorded} == {"kokoro"}
    for scene, _, call in recorded:
        assert (call["rate"] == WHISPER["rate"]) == (scene == "scene_2"), scene


def test_a_rewritten_script_records_fresh_in_the_same_engine(small_project):
    """New lines can't inherit a scene's old voice (scene 2 is a different
    scene now), but the engine the film was made with still holds."""
    state, sm = small_project(**FILM)
    agent, calls = _agent(sm)
    _edit(agent, state, "make the voices in scene 2 whispered")

    latest = sm.latest(state.project_id)
    for cfg in latest.audio.voice_configs:
        cfg.engine = "kokoro"
    latest.script.scenes[1].dialogue[0].text = "An entirely new line."
    executor = EditExecutor()
    calls = silence_tts(executor.audio.tools)
    executor.execute(latest, EditStep("rerun_audio", "audio", "global"))

    assert latest.audio.scene_voices == {}
    assert {c["engine"] for c in calls} == {"kokoro"}
    assert all(c["rate"] != WHISPER["rate"] for c in calls)
    assert "An entirely new line." in {c["text"] for c in calls}


def test_scene_voice_changes_are_kept_in_the_saved_state(small_project):
    state, sm = small_project(**FILM)
    agent, _ = _agent(sm)
    _edit(agent, state, "make the voices in scene 2 whispered")
    saved = sm.latest(state.project_id).audio
    assert set(saved.scene_voices) == {"scene_2"}
    assert set(saved.scene_voices["scene_2"]) == {"char_protagonist", "char_supporting"}
    assert saved.voice_for("scene_2", "char_protagonist").tone == "whispered"
    assert saved.voice_for("scene_3", "char_protagonist").tone != "whispered"


# ---- asking for it -----------------------------------------------------------

@pytest.mark.parametrize("query,intent,params", [
    ("make the voices in scene 2 whispered", "change_voice_tone", {"tone": "whispered"}),
    ("make the voices deeper", "change_voice_tone", {"tone": "deep"}),
    ("make the narrator warmer", "change_voice_tone", {"tone": "warm"}),
    ("change all the voices", "change_voice", {"voice": "alternate"}),
    # Unchanged: a story's tone is a script edit, not a voice.
    ("change the tone of the story to sad", "change_genre", None),
])
def test_voice_requests_are_understood_in_plain_phrasing(query, intent, params):
    out = classify(query, ["scene_1", "scene_2"], ["char_narrator", "char_protagonist"],
                   {"char_narrator": "Narrator", "char_protagonist": "Aria"})
    assert out.intent == intent
    if params is not None:
        assert out.parameters == params


@pytest.mark.parametrize("given,expected", [
    ("whisper", "whispered"), ("Whispering", "whispered"),
    ("deeper", "deep"), ("happy", "cheerful"),
])
def test_tone_names_a_model_might_use_are_understood(given, expected):
    assert AudioAgent.normalise_voice_params({"tone": given})["tone"] == expected


def test_an_unknown_tone_fails_instead_of_doing_nothing(small_project):
    """An unrecognised tone used to set the label and re-record the lines
    unchanged, and the edit reported success."""
    state, sm = small_project(**FILM)
    executor = EditExecutor()
    calls = silence_tts(executor.audio.tools)
    latest = sm.latest(state.project_id)
    with pytest.raises(ValueError, match="jazzy"):
        executor.execute(latest, EditStep("rerun_audio", "audio", "scene:scene_2",
                                          {"tone": "jazzy"}))
    assert calls == []
