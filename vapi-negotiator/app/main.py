"""FastAPI application for Vapi Negotiator.

Provides REST control endpoints for managing procurement briefs, vendor personas,
assistant registration, competitive call rounds, call histories, and serving the web demo.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse

from app.assistants import register_all_assistants
from app.call_manager import CallManager
from app.config import BASE_DIR, settings
from app.models import Brief, CreateCallRequest, LaunchRoundRequest
from app.store import Store
from app.vapi_client import VapiClient
from app.webhook import router as webhook_router


def load_json_file(file_path: Path, default: Any) -> Any:
    """Helper to safely read JSON configuration files."""
    if not file_path.exists():
        return default
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager initializing clients, store, and manager on startup."""
    # Ensure folders
    settings.ensure_directories()

    # Load persistent store
    store = Store(settings.store_file_path)

    # Load configs
    brief_data = load_json_file(settings.brief_file_path, {
        "item": "Industrial Equipment",
        "quantity": 50,
        "target_price_per_unit": 1200.0,
        "walk_away_price_per_unit": 1450.0,
        "starting_offer_per_unit": 1000.0,
    })
    vendors_data = load_json_file(settings.vendors_file_path, [])

    # Initialize client & manager
    vapi_client = VapiClient(
        api_key=settings.vapi_api_key,
        base_url=settings.vapi_base_url,
    )
    call_manager = CallManager(
        vapi_client=vapi_client,
        store=store,
        brief=brief_data,
        vendors=vendors_data,
        phone_number_id=settings.vapi_phone_number_id,
    )

    # Attach to app state
    app.state.store = store
    app.state.brief = brief_data
    app.state.vendors = vendors_data
    app.state.vapi_client = vapi_client
    app.state.call_manager = call_manager

    yield

    # Clean shutdown
    await vapi_client.close()


app = FastAPI(
    title="Vapi Negotiator API",
    description="Automated AI Voice Negotiation Platform with Vapi.ai",
    version="1.0.0",
    lifespan=lifespan,
)

# Enable CORS for browser demo & cross-origin tools
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include webhook router
app.include_router(webhook_router)


# -----------------------------------------------------------------------------
# Web Demo & HTML Serving
# -----------------------------------------------------------------------------
@app.get("/web_call_demo.html", response_class=FileResponse)
@app.get("/web-call-demo.html", response_class=FileResponse)
@app.get("/demo", response_class=FileResponse)
async def serve_demo():
    """Serve the interactive web audio negotiation demo."""
    demo_file = BASE_DIR / "web_call_demo.html"
    if not demo_file.exists():
        demo_file = BASE_DIR / "web-call-demo.html"
    if not demo_file.exists():
        raise HTTPException(status_code=404, detail="web_call_demo.html not found")
    return FileResponse(str(demo_file), media_type="text/html")


# -----------------------------------------------------------------------------
# Health & Configuration Endpoints
# -----------------------------------------------------------------------------
@app.get("/")
async def root() -> Dict[str, Any]:
    """Root health and system status endpoint."""
    return {
        "service": "Vapi Negotiator",
        "status": "online",
        "docs_url": "/docs",
        "demo_url": "/web_call_demo.html",
        "has_vapi_key": bool(settings.vapi_api_key and settings.vapi_api_key != "your_private_vapi_api_key_here"),
        "webhook_url": settings.webhook_url,
    }


@app.get("/api/config")
async def get_client_config() -> Dict[str, Any]:
    """Expose safe client configuration (e.g. public key for web calls)."""
    assistants = await app.state.store.get_all_assistants()
    return {
        "vapi_public_key": settings.vapi_public_key,
        "webhook_url": settings.webhook_url,
        "brief": app.state.brief,
        "vendors": app.state.vendors,
        "assistants": assistants,
    }


# -----------------------------------------------------------------------------
# Brief & Vendors Endpoints
# -----------------------------------------------------------------------------
@app.get("/api/brief")
async def get_brief() -> Dict[str, Any]:
    """Retrieve current buyer procurement brief."""
    return app.state.brief


@app.put("/api/brief")
async def update_brief(brief: Brief) -> Dict[str, Any]:
    """Update buyer procurement brief and persist to disk."""
    app.state.brief = brief.model_dump()
    app.state.call_manager.brief = app.state.brief

    with open(settings.brief_file_path, "w", encoding="utf-8") as f:
        json.dump(app.state.brief, f, indent=2)

    return {"status": "updated", "brief": app.state.brief}


@app.get("/api/vendors")
async def get_vendors() -> List[Dict[str, Any]]:
    """Retrieve list of vendor personas."""
    return app.state.vendors


# -----------------------------------------------------------------------------
# Assistants Management Endpoints
# -----------------------------------------------------------------------------
@app.get("/api/assistants")
async def get_assistants() -> Dict[str, Any]:
    """Retrieve registered buyer and vendor assistants."""
    return await app.state.store.get_all_assistants()


@app.post("/api/bootstrap")
async def bootstrap_assistants() -> Dict[str, Any]:
    """Register or refresh all assistants on Vapi.ai."""
    if not settings.vapi_api_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="VAPI_API_KEY is not configured in .env",
        )

    result = await register_all_assistants(
        vapi_client=app.state.vapi_client,
        store=app.state.store,
        brief=app.state.brief,
        vendors=app.state.vendors,
        webhook_url=settings.webhook_url,
        webhook_secret=settings.webhook_secret,
        provider=settings.default_model_provider,
        model=settings.default_model,
    )
    return {"status": "bootstrapped", "assistants": result}


# -----------------------------------------------------------------------------
# Calls & Rounds Management Endpoints
# -----------------------------------------------------------------------------
@app.post("/api/calls")
async def launch_call(req: CreateCallRequest) -> Dict[str, Any]:
    """Launch a negotiation call to a single vendor."""
    call_record = await app.state.call_manager.launch_call(
        vendor_id=req.vendor_id,
        phone_number=req.phone_number,
        simulated=req.simulated,
    )
    return {"status": "launched", "call": call_record.model_dump()}


@app.get("/api/calls")
async def list_calls() -> List[Dict[str, Any]]:
    """List all tracked call records."""
    records = await app.state.store.get_all_calls()
    return [r.model_dump() for r in records]


@app.get("/api/calls/{call_id}")
async def get_call(call_id: str) -> Dict[str, Any]:
    """Get full details, transcript turns, and outcome for a specific call."""
    record = await app.state.store.get_call(call_id)
    if not record:
        raise HTTPException(status_code=404, detail="Call record not found")
    return record.model_dump()


@app.post("/api/rounds/launch")
async def launch_round(req: LaunchRoundRequest) -> Dict[str, Any]:
    """Launch a multi-vendor negotiation round."""
    round_record = await app.state.call_manager.launch_round(
        vendor_ids=req.vendor_ids,
        concurrency=req.concurrency,
        simulated=req.simulated,
    )
    return {"status": "round_initiated", "round": round_record.model_dump()}


@app.get("/api/rounds")
async def list_rounds() -> List[Dict[str, Any]]:
    """List all negotiation round scorecards."""
    records = await app.state.store.get_all_rounds()
    return [r.model_dump() for r in records]


@app.get("/api/rounds/{round_id}")
async def get_round(round_id: str) -> Dict[str, Any]:
    """Get scorecard and winning vendor for a specific round."""
    record = await app.state.store.get_round(round_id)
    if not record:
        raise HTTPException(status_code=404, detail="Round record not found")
    return record.model_dump()
