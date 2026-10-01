"""Per-project WebSocket progress stream."""
from __future__ import annotations
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from auth import accounts
from auth.deps import websocket_user

from ..services import progress

router = APIRouter()

# Close codes the browser sees. 1008 is "policy violation", which is what the
# WebSocket spec gives us for "you are not allowed to watch this".
POLICY_VIOLATION = 1008


@router.websocket("/progress/{project_id}")
async def progress_ws(ws: WebSocket, project_id: str):
    # The session cookie rides along with the handshake, so the socket is
    # authenticated the same way every other route is. Without this, progress
    # for any project was readable by anyone who guessed its id.
    user = await websocket_user(ws)
    if user is None:
        await ws.close(code=POLICY_VIOLATION, reason="sign in to continue")
        return
    if not accounts.may_access(user, project_id):
        await ws.close(code=POLICY_VIOLATION, reason="not found")
        return

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
