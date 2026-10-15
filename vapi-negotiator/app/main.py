"""FastAPI app: webhook receiver + a small control API for the Vapi layer.

Run:  uvicorn app.main:app --reload --port 8000
Docs: http://localhost:8000/docs
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .call_manager import CallManager, load_json
from .config import Settings
from .models import CallRecord
from .store import JsonStore
from .vapi_client import VapiClient, VapiError
from .webhook import router as webhook_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


class LaunchRequest(BaseModel):
    round: int = 1
    vendor_ids: list[str] | None = None
    leverage: dict[str, str] | None = None   # vendor_id -> real competing quote sentence


def build_manager(settings: Settings, client: VapiClient | None = None) -> CallManager:
    return CallManager(
        client=client,
        settings=settings,
        state_store=JsonStore(settings.state_dir / "state.json"),
        calls_store=JsonStore(settings.state_dir / "calls.json"),
        vendors=load_json(settings.config_dir / "vendors.json"),
        brief=load_json(settings.config_dir / "brief.json"),
    )


def create_app(settings: Settings | None = None, manager: CallManager | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        owned: VapiClient | None = None
        if app.state.manager.client is None and settings.vapi_api_key:
            owned = VapiClient(settings.vapi_api_key, settings.vapi_base_url)
            app.state.manager.client = owned
        if not settings.webhook_secret:
            logging.warning("VAPI_WEBHOOK_SECRET is empty: webhook is UNAUTHENTICATED (dev only)")
        yield
        if owned:
            await owned.aclose()

    app = FastAPI(title="Negotiator: Vapi layer", lifespan=lifespan)
    app.state.settings = settings
    app.state.manager = manager or build_manager(settings)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],  # Vite dev server
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(webhook_router)

    @app.get("/health")
    async def health(request: Request):
        m: CallManager = request.app.state.manager
        state = m.state_store.load()
        return {
            "ok": True,
            "vapi_key_configured": bool(settings.vapi_api_key),
            "webhook_url": settings.webhook_url or None,
            "bootstrapped": bool(state.get("buyer_assistant_id") and state.get("buyer_phone_number_id")),
            "vendors": list(m.vendors),
        }

    @app.post("/rounds/launch", response_model=list[CallRecord])
    async def launch(req: LaunchRequest, request: Request):
        m: CallManager = request.app.state.manager
        unknown = [v for v in (req.vendor_ids or []) if v not in m.vendors]
        if unknown:
            raise HTTPException(404, f"unknown vendor ids: {unknown}")
        try:
            return await m.launch_round(req.round, req.vendor_ids, req.leverage)
        except VapiError as exc:
            raise HTTPException(502, str(exc))

    @app.get("/calls", response_model=list[CallRecord])
    async def list_calls(request: Request, round: int | None = None):
        recs = list(request.app.state.manager.records.values())
        if round is not None:
            recs = [r for r in recs if r.round == round]
        return sorted(recs, key=lambda r: r.created_at)

    @app.get("/calls/{key}", response_model=CallRecord)
    async def get_call(key: str, request: Request):
        rec = request.app.state.manager.get(key)
        if rec is None:
            raise HTTPException(404, "call not found")
        return rec

    @app.post("/calls/{key}/sync", response_model=CallRecord)
    async def sync_call(key: str, request: Request):
        """Fallback when a webhook was missed: pull the call from Vapi's API."""
        m: CallManager = request.app.state.manager
        if m.get(key) is None:
            raise HTTPException(404, "call not found")
        try:
            return await m.sync_from_api(key)
        except VapiError as exc:
            raise HTTPException(502, str(exc))

    return app


app = create_app()