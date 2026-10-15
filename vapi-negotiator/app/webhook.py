"""Receiver for Vapi server messages (POST /vapi/webhook).

Every request body looks like {"message": {"type": "...", "call": {...}, ...}}.
We answer fast (Vapi expects quick responses) and do slow work in background tasks.
"""
from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request

router = APIRouter()
log = logging.getLogger("negotiator.webhook")


def _authorized(expected: str, x_vapi_secret: str | None, authorization: str | None) -> bool:
    """Accept the secret as X-Vapi-Secret or as 'Authorization: Bearer <secret>'."""
    if not expected:
        return True  # no secret configured (dev only!)
    candidates = [x_vapi_secret or ""]
    if authorization and authorization.lower().startswith("bearer "):
        candidates.append(authorization[7:])
    return any(hmac.compare_digest(expected, c) for c in candidates)


@router.post("/vapi/webhook")
async def vapi_webhook(
    request: Request,
    background: BackgroundTasks,
    x_vapi_secret: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
):
    settings = request.app.state.settings
    manager = request.app.state.manager

    if not _authorized(settings.webhook_secret, x_vapi_secret, authorization):
        raise HTTPException(status_code=401, detail="invalid webhook secret")

    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="body is not valid JSON")

    msg = body.get("message") or {}
    mtype = msg.get("type")

    if mtype == "status-update":
        manager.on_status_update(msg)
    elif mtype == "transcript":
        manager.on_transcript(msg)
    elif mtype == "end-of-call-report":
        rec = manager.on_end_of_call_report(msg)
        if rec is not None:
            # Your extraction / scoring runs here, AFTER we have replied to Vapi.
            background.add_task(manager.dispatch_completion, rec)
    elif mtype == "hang":
        log.warning("assistant hang detected (slow model/tool response)")
    else:
        log.debug("ignored webhook type: %s", mtype)

    # No webhook type we handle needs a response body, so an empty OK is correct.
    return {"ok": True}