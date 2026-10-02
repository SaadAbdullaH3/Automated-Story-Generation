"""M8 — an edit is something the editor can do, or it is refused.

A live run classified "make the voices in scene 2 whispered" as intent
`whisper_voices` with `{"voice_type": "whisper"}`: the prompt asked for "a short
snake_case action name" and free-form parameters, so the model made both up.
The planner fell back to re-recording, the executor ignored the unknown key,
the same lines were recorded in the same voice, and the creator was told
"Change made". Gemini, meanwhile, refused the schema outright: an open
`parameters` dict becomes `additionalProperties`, which its developer API
rejects, so every edit fell through to the next provider.
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from agents.edit_agent import EditAgent
from agents.edit_agent.intent_classifier import IntentClassifier
from agents.edit_agent.planner import plan
from agents.edit_agent.vocabulary import UNCLEAR_MESSAGE, EditDraft
from shared.schemas.edit import EditCommand, EditIntent
from tests.conftest import silence_tts

CONTEXT = (["scene_1", "scene_2"], ["char_marco", "char_lina"],
           {"char_marco": "Marco", "char_lina": "Lina"},
           {"scene_1": "Market Clash", "scene_2": "Recipe Reveal"})


class FakeModel:
    provider = "fake"

    def __init__(self, answer):
        self.answer = answer
        self.prompts: list = []

    def generate_structured(self, prompt, schema, system="", temperature=0.5, **_):
        self.prompts.append(prompt)
        if isinstance(self.answer, Exception):
            raise self.answer
        return schema.model_validate(self.answer)


def answering(answer) -> IntentClassifier:
    clf = IntentClassifier()
    clf.llm = FakeModel(answer)
    return clf


# ---- the form the model fills in ------------------------------------------------

def test_the_answer_from_the_live_run_does_not_fit_the_form():
    with pytest.raises(ValidationError):
        EditDraft.model_validate({"intent": "whisper_voices", "scope": "scene:scene_2",
                                  "parameters": {"voice_type": "whisper"}})
    with pytest.raises(ValidationError):
        EditDraft.model_validate({"intent": "change_voice_tone", "tone": "whisper-y"})


def test_the_form_has_nothing_gemini_refuses():
    assert "additionalProperties" not in json.dumps(EditDraft.model_json_schema())


def test_the_prompt_names_every_choice_and_the_scenes_by_title():
    clf = answering({"intent": "remove_subtitles"})
    clf.classify("drop the subtitles", *CONTEXT)
    prompt = clf.llm.prompts[0]
    for word in ("change_voice_tone", "apply_filter", "unclear", "whispered", "noir",
                 "scene_2 (Recipe Reveal)", "char_lina (Lina)"):
        assert word in prompt, word


@pytest.mark.parametrize("answer,expected", [
    ({"intent": "change_voice_tone", "scope": "scene:2", "tone": "whispered"},
     ("change_voice_tone", "audio", "scene:scene_2", {"tone": "whispered"})),
    ({"intent": "change_voice_tone", "scope": "character:Lina", "tone": "cheerful"},
     ("change_voice_tone", "audio", "character:char_lina", {"tone": "cheerful"})),
    ({"intent": "adjust_scene_aesthetic", "scope": "scene 2", "aesthetic": "darker"},
     ("adjust_scene_aesthetic", "video_frame", "scene:scene_2", {"aesthetic": "darker"})),
    ({"intent": "change_voice", "scope": None},
     ("change_voice", "audio", "global", {"voice": "alternate"})),
])
def test_a_models_answer_arrives_in_the_editors_own_names(answer, expected):
    out = answering(answer).classify("whatever was asked", *CONTEXT)
    assert (out.intent, out.target, out.scope, out.parameters) == expected


def test_what_the_model_gives_up_on_the_keyword_rules_can_still_catch():
    out = answering({"intent": "unclear"}).classify(
        "make the voices in scene 2 whispered", *CONTEXT)
    assert (out.intent, out.scope, out.parameters) == (
        "change_voice_tone", "scene:scene_2", {"tone": "whispered"})


def test_a_model_that_is_down_leaves_the_keyword_rules():
    out = answering(RuntimeError("503")).classify("apply the noir filter", *CONTEXT)
    assert (out.intent, out.parameters) == ("apply_filter", {"filter": "noir"})


# ---- the planner only plans what can be done ---------------------------------------

@pytest.mark.parametrize("name", ["whisper_voices", "generic_edit", "unclear"])
def test_an_edit_nobody_can_name_is_refused(name):
    with pytest.raises(ValueError, match="not sure what to change"):
        plan(EditIntent(intent=name, target="audio"))


def test_an_edit_missing_what_it_needs_asks_for_it():
    with pytest.raises(ValueError, match="which tone\\? whispered, soft"):
        plan(EditIntent(intent="change_voice_tone", target="audio", parameters={}))
    with pytest.raises(ValueError, match="which look\\?"):
        plan(EditIntent(intent="apply_filter", target="video_frame", parameters={}))


# ---- end to end: refused, and nothing touched -----------------------------------------

@pytest.mark.parametrize("query,reason", [
    ("make it better", UNCLEAR_MESSAGE),
    ("change the narrator's tone", "which tone?"),
])
def test_a_request_that_cant_be_carried_out_changes_nothing(small_project, query, reason):
    """Both used to re-record or recut the film unchanged and report success."""
    state, sm = small_project()
    agent = EditAgent(sm)
    calls = silence_tts(agent.executor.audio.tools)

    result = agent.edit(EditCommand(project_id=state.project_id, query=query))
    assert not result.success
    assert result.error.startswith(reason)
    assert calls == []
    assert sm.latest(state.project_id).version == 1
