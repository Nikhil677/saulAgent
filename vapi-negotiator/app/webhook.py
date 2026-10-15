"""Vapi Webhook Endpoint.

Receives, authenticates, and dispatches real-time call events, speech updates,
and end-of-call reports from Vapi.ai servers.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from fastapi import APIRouter, Header, HTTPException, Request, status

from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vapi", tags=["Webhook"])


@router.post("/webhook")
async def receive_vapi_webhook(
    request: Request,
    x_vapi_secret: Optional[str] = Header(None, alias="x-vapi-secret"),
) -> Dict[str, Any]:
    """Receive and process real-time call webhook events from Vapi."""
    # Optional webhook secret verification
    if settings.webhook_secret and x_vapi_secret != settings.webhook_secret:
        # Check authorization header fallback
        auth_header = request.headers.get("authorization", "")
        if auth_header != f"Bearer {settings.webhook_secret}" and x_vapi_secret != settings.webhook_secret:
            logger.warning("Unauthorized webhook request rejected.")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing webhook secret.",
            )

    try:
        body: Dict[str, Any] = await request.json()
    except Exception as e:
        logger.error("Failed to decode webhook JSON: %s", e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed JSON body",
        )

    message = body.get("message", body)
    message_type = message.get("type", "unknown")
    logger.info("Received Vapi webhook message: type=%s", message_type)

    # Handle dynamic assistant request if requested by Vapi
    if message_type == "assistant-request":
        call_manager = getattr(request.app.state, "call_manager", None)
        buyer_assistant = await request.app.state.store.get_buyer_assistant()
        if buyer_assistant and "payload" in buyer_assistant:
            return {"assistant": buyer_assistant["payload"]}
        return {}

    call_manager = getattr(request.app.state, "call_manager", None)
    if not call_manager:
        logger.warning("CallManager not initialized on app state.")
        return {"status": "uninitialized"}

    result = await call_manager.handle_webhook_event(body)
    return {"status": "ok", "result": result}
