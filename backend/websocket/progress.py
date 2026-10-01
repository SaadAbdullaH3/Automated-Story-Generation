"""Per-project WebSocket progress stream."""
from __future__ import annotations
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..services import progress

router = APIRouter()


@router.websocket("/progress/{project_id}")
async def progress_ws(ws: WebSocket, project_id: str):
    await ws.accept()
    try:
        # The stream opens with a snapshot so a client that connects late (or
        # reconnects after a restart) catches up, then follows the job to its end.
        async for envelope in progress.stream(project_id):
            await ws.send_text(json.dumps(envelope))
    except WebSocketDisconnect:
        pass
    finally:
        try:
            await ws.close()
        except RuntimeError:
            pass
