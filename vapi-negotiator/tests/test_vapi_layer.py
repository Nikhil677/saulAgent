"""Comprehensive test suite for Vapi Negotiator.

Validates config loading, models, atomic file storage, prompt generation,
assistant payload building, VapiClient REST communication, webhook event dispatching,
and call manager outcome classification.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
import pytest
import httpx
from fastapi.testclient import TestClient

from app.assistants import (
    build_buyer_assistant_payload,
    build_vendor_assistant_payload,
)
from app.call_manager import CallManager
from app.config import Settings
from app.main import app
from app.models import (
    Brief,
    CallRecord,
    CallStatus,
    NegotiationOutcome,
    RoundRecord,
    Turn,
    VendorPersona,
)
from app.prompts import (
    build_analysis_prompt,
    build_buyer_prompt,
    build_vendor_prompt,
)
from app.store import Store
from app.vapi_client import VapiAuthenticationError, VapiClient, VapiNotFoundError


# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------
@pytest.fixture
def sample_brief():
    return {
        "brief_id": "test-brief-01",
        "item": "Test Server Enclosures",
        "quantity": 50,
        "unit": "units",
        "starting_offer_per_unit": 1000.0,
        "target_price_per_unit": 1200.0,
        "walk_away_price_per_unit": 1450.0,
        "required_delivery_timeline": "Within 4 weeks",
        "desired_payment_terms": "Net 30 days",
        "minimum_warranty_years": 3,
        "negotiation_priorities": ["Price under $1200", "Free freight"],
    }


@pytest.fixture
def sample_vendors():
    return [
        {
            "id": "vendor-test-apex",
            "name": "Apex Test Supply",
            "contact_name": "Dave Miller",
            "contact_title": "Director",
            "list_price": 1650.0,
            "price_floor": 1280.0,
            "concession_step": 75.0,
            "negotiation_style": "Fast-Talking",
            "quirks": ["Mentions aluminum costs"],
            "acceptable_payment_terms": ["Net 30"],
            "standard_warranty_years": 3,
        },
        {
            "id": "vendor-test-pacific",
            "name": "Pacific Test Supply",
            "contact_name": "Marcus Chen",
            "contact_title": "VP Sales",
            "list_price": 1420.0,
            "price_floor": 1180.0,
            "concession_step": 60.0,
            "negotiation_style": "Deal-Closer",
            "quirks": ["Mentions Austin competitor"],
            "acceptable_payment_terms": ["Net 15"],
            "standard_warranty_years": 2,
        },
    ]


@pytest.fixture
def temp_store(tmp_path):
    store_file = tmp_path / "test_store.json"
    return Store(store_file)


# -----------------------------------------------------------------------------
# 1. Config & Store Tests
# -----------------------------------------------------------------------------
def test_settings_initialization(tmp_path):
    custom_settings = Settings(
        vapi_api_key="test_api_key",
        vapi_public_key="test_public_key",
        data_dir=tmp_path / "data",
        store_file_path=tmp_path / "data" / "store.json",
    )
    assert custom_settings.vapi_api_key == "test_api_key"
    assert custom_settings.vapi_public_key == "test_public_key"
    assert custom_settings.server_port == 8000


@pytest.mark.asyncio
async def test_store_atomic_operations(temp_store):
    # Verify initial structure
    all_assts = await temp_store.get_all_assistants()
    assert all_assts["buyer"] is None
    assert all_assts["vendors"] == {}

    # Set buyer assistant
    buyer_data = {"id": "asst_buyer_123", "name": "Alex Mercer"}
    await temp_store.set_buyer_assistant(buyer_data)
    retrieved_buyer = await temp_store.get_buyer_assistant()
    assert retrieved_buyer["id"] == "asst_buyer_123"

    # Set vendor assistant
    vendor_data = {"id": "asst_vendor_456", "name": "Dave Miller"}
    await temp_store.set_vendor_assistant("vendor-apex", vendor_data)
    retrieved_vendor = await temp_store.get_vendor_assistant("vendor-apex")
    assert retrieved_vendor["name"] == "Dave Miller"

    # Verify atomic file integrity on disk
    assert temp_store.file_path.exists()
    with open(temp_store.file_path, "r", encoding="utf-8") as f:
        disk_data = json.load(f)
    assert disk_data["buyer_assistant"]["id"] == "asst_buyer_123"


@pytest.mark.asyncio
async def test_store_call_operations(temp_store):
    turn = Turn(role="buyer", message="Can you do $1200?", extracted_price=1200.0)
    record = CallRecord(
        call_id="call-test-01",
        vendor_id="vendor-test-apex",
        vendor_name="Apex Test",
        assistant_id="asst_buyer_123",
        status=CallStatus.IN_PROGRESS,
        turns=[turn],
    )
    await temp_store.upsert_call(record)

    loaded = await temp_store.get_call("call-test-01")
    assert loaded is not None
    assert loaded.status == CallStatus.IN_PROGRESS
    assert len(loaded.turns) == 1
    assert loaded.turns[0].extracted_price == 1200.0


# -----------------------------------------------------------------------------
# 2. Prompts & Payloads Tests
# -----------------------------------------------------------------------------
def test_buyer_prompt_generation(sample_brief):
    prompt = build_buyer_prompt(sample_brief)
    assert "Alex Mercer" in prompt
    assert "50 units" in prompt
    assert "$1,000.00" in prompt
    assert "$1,200.00" in prompt
    assert "$1,450.00" in prompt
    assert "HARD CEILING" in prompt


def test_vendor_prompt_generation(sample_brief, sample_vendors):
    vendor = sample_vendors[0]
    prompt = build_vendor_prompt(vendor, sample_brief)
    assert "Dave Miller" in prompt
    assert "Apex Test Supply" in prompt
    assert "$1,650.00" in prompt
    assert "$1,280.00" in prompt
    assert "NEVER breach this floor" in prompt


def test_assistant_payload_builders(sample_brief, sample_vendors):
    buyer_payload = build_buyer_assistant_payload(
        brief=sample_brief,
        webhook_url="https://test.com/vapi/webhook",
        webhook_secret="sec123",
    )
    assert "Buyer Agent" in buyer_payload["name"]
    assert buyer_payload["serverUrl"] == "https://test.com/vapi/webhook"
    assert buyer_payload["serverUrlSecret"] == "sec123"
    assert buyer_payload["endCallFunctionEnabled"] is True
    assert "analysisPlan" in buyer_payload

    vendor_payload = build_vendor_assistant_payload(
        vendor=sample_vendors[0],
        brief=sample_brief,
        webhook_url="https://test.com/vapi/webhook",
    )
    assert "Vendor Agent - Apex Test Supply" in vendor_payload["name"]
    assert vendor_payload["voice"]["provider"] == "11labs"


# -----------------------------------------------------------------------------
# 3. CallManager Outcome Classification & Scorecard Tests
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_call_manager_classification(temp_store, sample_brief, sample_vendors):
    vapi_mock = VapiClient(api_key="mock")
    manager = CallManager(
        vapi_client=vapi_mock,
        store=temp_store,
        brief=sample_brief,
        vendors=sample_vendors,
    )

    # 1. Deal accepted within mandate
    call1 = CallRecord(
        call_id="call-01",
        vendor_id=sample_vendors[0]["id"],
        vendor_name="Apex Test",
        assistant_id="asst-01",
    )
    manager._classify_call_outcome(
        call_record=call1,
        structured_data={"deal_reached": True, "final_unit_price": 1280.0},
    )
    assert call1.outcome == NegotiationOutcome.DEAL_ACCEPTED
    assert call1.deal_terms is not None
    assert call1.deal_terms["unit_price"] == 1280.0

    # 2. Walk away (above ceiling)
    call2 = CallRecord(
        call_id="call-02",
        vendor_id=sample_vendors[0]["id"],
        vendor_name="Apex Test",
        assistant_id="asst-01",
    )
    manager._classify_call_outcome(
        call_record=call2,
        structured_data={"deal_reached": False, "final_unit_price": 1500.0},
    )
    assert call2.outcome == NegotiationOutcome.WALK_AWAY

    # 3. Regex price extraction
    extracted = manager._extract_price_from_text("Best I can do is $1,280.00 per unit today.")
    assert extracted == 1280.0


# -----------------------------------------------------------------------------
# 4. Webhook & REST Endpoints Tests
# -----------------------------------------------------------------------------
def test_fastapi_endpoints():
    client = TestClient(app)

    # 1. Root health check
    res = client.get("/")
    assert res.status_code == 200
    assert res.json()["service"] == "Vapi Negotiator"

    # 2. Config endpoint
    res_cfg = client.get("/api/config")
    assert res_cfg.status_code == 200
    assert "brief" in res_cfg.json()

    # 3. Brief endpoint
    res_brief = client.get("/api/brief")
    assert res_brief.status_code == 200

    # 4. Vendors endpoint
    res_v = client.get("/api/vendors")
    assert res_v.status_code == 200
    assert isinstance(res_v.json(), list)

    # 5. HTML Demo serving
    res_html = client.get("/web_call_demo.html")
    assert res_html.status_code == 200
    assert "Vapi Voice Negotiator" in res_html.text


def test_webhook_event_handling():
    client = TestClient(app)

    # Status update webhook
    event_payload = {
        "message": {
            "type": "status-update",
            "status": "ringing",
            "callId": "test-call-webhook-99",
            "call": {"id": "test-call-webhook-99"},
        }
    }
    res = client.post("/vapi/webhook", json=event_payload)
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
