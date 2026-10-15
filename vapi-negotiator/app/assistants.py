"""Build and register Vapi assistants + phone numbers (idempotent bootstrap).

Creates:
  * one BUYER assistant (the negotiator) + one outbound phone number
  * one assistant per SIMULATED vendor, each attached to its own free Vapi number,
    so the buyer can phone it exactly like a real shop.

Ids are saved in state/state.json so re-running updates instead of duplicating.
"""
from __future__ import annotations

import asyncio
from typing import Any

from .config import Settings
from .prompts import BUYER_FIRST_MESSAGE, BUYER_SYSTEM_PROMPT, build_vendor_prompt
from .store import JsonStore
from .vapi_client import VapiClient, VapiError

SERVER_MESSAGES = ["status-update", "transcript", "end-of-call-report", "hang"]


# --------------------------------------------------------------------- blocks
def _model_block(s: Settings, system_prompt: str, temperature: float) -> dict:
    if not s.llm_model:
        raise ValueError("Set VAPI_LLM_MODEL in .env (copy a valid model name from the Vapi dashboard).")
    return {
        "provider": s.llm_provider,
        "model": s.llm_model,
        "temperature": temperature,
        "messages": [{"role": "system", "content": system_prompt}],
    }


def _transcriber_block(s: Settings) -> dict:
    return {"provider": s.transcriber_provider, "model": s.transcriber_model,
            "language": s.transcriber_language}


def _server_block(s: Settings) -> dict:
    """Webhook config. If your Vapi version expects a nested `server` object instead of the
    flat serverUrl/serverUrlSecret fields, change it here: this is the only place."""
    if not s.webhook_url:
        return {}
    block: dict[str, Any] = {"serverUrl": s.webhook_url, "serverMessages": SERVER_MESSAGES}
    if s.webhook_secret:
        block["serverUrlSecret"] = s.webhook_secret
    return block


# ----------------------------------------------------------------- assistants
def build_buyer_assistant(s: Settings) -> dict:
    return {
        "name": "negotiator-buyer",
        "firstMessage": BUYER_FIRST_MESSAGE,
        "model": _model_block(s, BUYER_SYSTEM_PROMPT, temperature=0.4),
        "voice": {"provider": s.buyer_voice_provider, "voiceId": s.buyer_voice_id},
        "transcriber": _transcriber_block(s),
        "maxDurationSeconds": s.max_call_seconds,
        "silenceTimeoutSeconds": s.silence_timeout_seconds,
        "endCallFunctionEnabled": True,
        "backgroundDenoisingEnabled": True,
        **_server_block(s),
    }


def build_vendor_assistant(s: Settings, vendor: dict) -> dict:
    return {
        "name": f"sim-vendor-{vendor['id']}",
        # Vendor waits for the buyer's greeting; otherwise both would talk at the same time.
        "firstMessageMode": "assistant-waits-for-user",
        "model": _model_block(s, build_vendor_prompt(vendor), temperature=0.7),
        "voice": {"provider": s.vendor_voice_provider, "voiceId": vendor.get("voice_id", s.vendor_voice_id)},
        "transcriber": _transcriber_block(s),
        "maxDurationSeconds": s.max_call_seconds + 30,
        "silenceTimeoutSeconds": s.silence_timeout_seconds,
        "endCallFunctionEnabled": True,
        "backgroundDenoisingEnabled": True,
        # Vendors do not send webhooks: only the buyer's call is recorded.
    }


# ---------------------------------------------------------------- bootstrap
async def _upsert_assistant(client: VapiClient, existing_id: str | None, payload: dict) -> str:
    if existing_id:
        try:
            await client.update_assistant(existing_id, payload)
            return existing_id
        except VapiError as exc:
            if exc.status != 404:
                raise
    created = await client.create_assistant(payload)
    return created["id"]


async def _create_number(client: VapiClient, s: Settings, name: str, assistant_id: str | None) -> dict:
    """Create a free Vapi (US) number and wait until the E.164 number is assigned."""
    payload: dict[str, Any] = {"provider": "vapi", "name": name,
                               "numberDesiredAreaCode": s.number_area_code}
    if assistant_id:
        payload["assistantId"] = assistant_id
    created = await client.create_phone_number(payload)
    for _ in range(30):  # up to ~60 s
        if created.get("number"):
            return created
        await asyncio.sleep(2)
        created = await client.get_phone_number(created["id"])
    raise VapiError(0, f"phone number {created.get('id')} was created but has no number yet")


async def bootstrap(client: VapiClient, s: Settings, vendors: list[dict],
                    store: JsonStore, create_numbers: bool = True) -> dict:
    state = store.load()
    state["buyer_assistant_id"] = await _upsert_assistant(
        client, state.get("buyer_assistant_id"), build_buyer_assistant(s))
    store.save(state)

    vstate = state.setdefault("vendors", {})
    for v in vendors:
        if v.get("mode", "simulated") != "simulated":
            continue  # real vendors need no assistant
        entry = vstate.setdefault(v["id"], {})
        entry["assistant_id"] = await _upsert_assistant(
            client, entry.get("assistant_id"), build_vendor_assistant(s, v))
        if create_numbers and not entry.get("phone_number_id"):
            num = await _create_number(client, s, f"vendor-{v['id']}", entry["assistant_id"])
            entry["phone_number_id"], entry["number"] = num["id"], num["number"]
        store.save(state)

    if create_numbers and not state.get("buyer_phone_number_id"):
        num = await _create_number(client, s, "buyer-outbound", None)
        state["buyer_phone_number_id"], state["buyer_number"] = num["id"], num["number"]
        store.save(state)
    return state