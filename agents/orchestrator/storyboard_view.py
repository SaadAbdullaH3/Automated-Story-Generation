"""What a storyboard card shows, in one place.

The creator interface draws a strip of scene cards. The same cards appear two
ways — streamed as progress events while a plan is still being drawn, and read
back from the storyboard endpoint when the page is reloaded — so they are built
here once, and both paths agree by construction rather than by care.

Everything on a card is real state: the scene's tone and the camera move that
tone will produce, and for each line the character and the voice that will
speak it. None of it is decoration.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from shared import providers, voices

from agents.video_agent import camera


def _voice_label(engine: str, voice_id: str) -> str:
    """'bm_george' -> 'George'. The voice tables carry a human label."""
    entry = voices.get(engine)
    if entry:
        for v in entry.voices:
            if v.id == voice_id and v.label:
                return v.label.split(" — ")[0].strip()
    return voice_id or ""


def scene_cards(script, engine: Optional[str] = None) -> List[Dict]:
    """One card per scene: title, tone, the opening camera move, and its lines."""
    from agents.audio_agent.agent import AudioAgent   # heavy import, kept local

    if script is None:
        return []
    if engine is None:
        spec = providers.active("tts")
        engine = spec.provider if spec else "edge"

    people = {c.id: c for c in script.characters.characters}
    spoken = {}
    for c in script.characters.characters:
        voice_id = AudioAgent.voice_for(c, engine)
        spoken[c.id] = {"name": c.name, "voice": _voice_label(engine, voice_id)}

    cards = []
    for scene in script.scenes:
        tone = getattr(scene, "tone", "") or ""
        cards.append({
            "scene_id": scene.scene_id,
            "index": scene.index,
            "title": scene.title,
            "setting": scene.setting,
            "tone": tone,
            # The establishing shot opens the scene, so its move is the one
            # worth naming on the card.
            "move": camera.move_for("establishing", tone, 0).replace("_", " "),
            "estimated_ms": scene.duration_ms,
            "lines": [
                {
                    "line_id": line.line_id,
                    "character": spoken.get(line.character_id, {}).get(
                        "name", people.get(line.character_id).name
                        if people.get(line.character_id) else line.character_id),
                    "voice": spoken.get(line.character_id, {}).get("voice", ""),
                    "text": line.text,
                }
                for line in scene.dialogue
            ],
        })
    return cards


def cast(script, engine: Optional[str] = None) -> List[Dict]:
    """Who is in the film and who will voice them.

    Also what the interface counts to state the render's cost honestly: every
    character gets a portrait, so the cast size is part of the image bill.
    """
    from agents.audio_agent.agent import AudioAgent

    if script is None:
        return []
    if engine is None:
        spec = providers.active("tts")
        engine = spec.provider if spec else "edge"
    return [
        {"character_id": c.id, "name": c.name, "role": c.role,
         "voice": _voice_label(engine, AudioAgent.voice_for(c, engine))}
        for c in script.characters.characters
    ]


def image_budget(script) -> Dict[str, int]:
    """How many images planning and rendering each cost, from the real script.

    Planning draws one preview per scene. Rendering draws a portrait per
    character plus the two framings per scene that the preview cannot stand in
    for — the wide shot reuses the preview.
    """
    if script is None:
        return {"plan": 0, "render": 0}
    scenes = len(script.scenes)
    return {"plan": scenes,
            "render": len(script.characters.characters) + 2 * scenes}
