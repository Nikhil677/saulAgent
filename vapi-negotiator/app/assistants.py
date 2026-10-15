"""Assistant payload builder and registration engine for Vapi Negotiator.

Constructs Vapi-compliant assistant specifications for both buyer and vendor personas,
configures speech synthesis, LLM parameters, webhooks, and syncs them with Vapi API and local store.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.models import Brief, VendorPersona
from app.prompts import build_analysis_prompt, build_buyer_prompt, build_vendor_prompt
from app.store import Store
from app.vapi_client import VapiClient

logger = logging.getLogger(__name__)


def build_buyer_assistant_payload(
    brief: Dict[str, Any],
    webhook_url: Optional[str] = None,
    webhook_secret: Optional[str] = None,
    provider: str = "openai",
    model: str = "gpt-4o",
    temperature: float = 0.7,
) -> Dict[str, Any]:
    """Construct Vapi assistant configuration for the AI Buyer Agent."""
    prompt = build_buyer_prompt(brief)
    item_name = brief.get("item", "Server Racks")
    quantity = brief.get("quantity", 50)

    payload: Dict[str, Any] = {
        "name": f"Buyer Agent - Alex Mercer ({item_name[:25]})",
        "model": {
            "provider": provider,
            "model": model,
            "temperature": temperature,
            "messages": [
                {
                    "role": "system",
                    "content": prompt,
                }
            ],
        },
        "voice": {
            "provider": "11labs",
            "voiceId": "burt",  # Professional, confident tone
        },
        "firstMessage": (
            f"Hello, this is Alex Mercer calling from Nexor Infrastructure. "
            f"I'm reaching out regarding a procurement requirement for {quantity} units of {item_name}. "
            f"Are you the right person to speak with on commercial volume pricing?"
        ),
        "endCallFunctionEnabled": True,
        "recordingEnabled": True,
        "silenceTimeoutSeconds": 25,
        "maxDurationSeconds": 600,
    }

    if webhook_url:
        payload["serverUrl"] = webhook_url
    if webhook_secret:
        payload["serverUrlSecret"] = webhook_secret

    payload["analysisPlan"] = {
        "summaryPrompt": (
            "Provide a concise 2-to-3 sentence summary of the negotiation call, "
            "noting whether a deal was struck, the final agreed unit price, and key agreed delivery/payment terms."
        ),
        "structuredDataPrompt": build_analysis_prompt(brief),
        "structuredDataSchema": {
            "type": "object",
            "properties": {
                "deal_reached": {"type": "boolean"},
                "final_unit_price": {"type": "number"},
                "agreed_quantity": {"type": "integer"},
                "total_contract_value": {"type": "number"},
                "agreed_delivery": {"type": "string"},
                "agreed_payment_terms": {"type": "string"},
                "agreed_warranty_years": {"type": "integer"},
                "outcome_summary": {"type": "string"},
            },
            "required": ["deal_reached"],
        },
    }

    return payload


def build_vendor_assistant_payload(
    vendor: Dict[str, Any],
    brief: Dict[str, Any],
    webhook_url: Optional[str] = None,
    webhook_secret: Optional[str] = None,
    provider: str = "openai",
    model: str = "gpt-4o",
    temperature: float = 0.7,
) -> Dict[str, Any]:
    """Construct Vapi assistant configuration for a simulated AI Vendor Sales Rep."""
    prompt = build_vendor_prompt(vendor, brief)
    voice_config = vendor.get("voice", {"provider": "11labs", "voiceId": "burt"})

    payload: Dict[str, Any] = {
        "name": f"Vendor Agent - {vendor.get('name')} ({vendor.get('contact_name')})",
        "model": {
            "provider": provider,
            "model": model,
            "temperature": temperature,
            "messages": [
                {
                    "role": "system",
                    "content": prompt,
                }
            ],
        },
        "voice": voice_config,
        "firstMessage": (
            f"Hello, thank you for calling {vendor.get('name')}. "
            f"This is {vendor.get('contact_name')}, {vendor.get('contact_title')}. "
            f"How can I help you with your equipment procurement today?"
        ),
        "endCallFunctionEnabled": True,
        "recordingEnabled": True,
        "silenceTimeoutSeconds": 25,
        "maxDurationSeconds": 600,
    }

    if webhook_url:
        payload["serverUrl"] = webhook_url
    if webhook_secret:
        payload["serverUrlSecret"] = webhook_secret

    payload["analysisPlan"] = {
        "summaryPrompt": (
            f"Summarize the negotiation from the perspective of {vendor.get('name')}, "
            f"stating if the deal closed, final agreed price, and if margins held above floor."
        ),
    }

    return payload


async def register_all_assistants(
    vapi_client: VapiClient,
    store: Store,
    brief: Dict[str, Any],
    vendors: List[Dict[str, Any]],
    webhook_url: Optional[str] = None,
    webhook_secret: Optional[str] = None,
    provider: str = "openai",
    model: str = "gpt-4o",
) -> Dict[str, Any]:
    """Register or update both buyer and vendor assistants on Vapi and save IDs to store."""
    results: Dict[str, Any] = {
        "buyer": None,
        "vendors": {},
    }

    # 1. Register / Update Buyer Assistant
    existing_buyer = await store.get_buyer_assistant()
    buyer_payload = build_buyer_assistant_payload(
        brief=brief,
        webhook_url=webhook_url,
        webhook_secret=webhook_secret,
        provider=provider,
        model=model,
    )

    try:
        if existing_buyer and existing_buyer.get("id"):
            buyer_id = existing_buyer["id"]
            logger.info("Updating existing buyer assistant: %s", buyer_id)
            updated = await vapi_client.update_assistant(buyer_id, buyer_payload)
            buyer_data = {
                "id": buyer_id,
                "name": updated.get("name", buyer_payload["name"]),
                "payload": buyer_payload,
            }
        else:
            logger.info("Creating new buyer assistant on Vapi")
            created = await vapi_client.create_assistant(buyer_payload)
            buyer_data = {
                "id": created["id"],
                "name": created.get("name", buyer_payload["name"]),
                "payload": buyer_payload,
            }

        await store.set_buyer_assistant(buyer_data)
        results["buyer"] = buyer_data
    except Exception as e:
        logger.error("Failed to register buyer assistant: %s", e)
        results["buyer"] = {"error": str(e)}

    # 2. Register / Update Vendor Assistants
    for vendor in vendors:
        v_id = vendor["id"]
        existing_v = await store.get_vendor_assistant(v_id)
        vendor_payload = build_vendor_assistant_payload(
            vendor=vendor,
            brief=brief,
            webhook_url=webhook_url,
            webhook_secret=webhook_secret,
            provider=provider,
            model=model,
        )

        try:
            if existing_v and existing_v.get("id"):
                v_assistant_id = existing_v["id"]
                logger.info("Updating existing vendor assistant %s: %s", v_id, v_assistant_id)
                updated_v = await vapi_client.update_assistant(v_assistant_id, vendor_payload)
                vendor_data = {
                    "id": v_assistant_id,
                    "vendor_id": v_id,
                    "name": updated_v.get("name", vendor_payload["name"]),
                    "payload": vendor_payload,
                }
            else:
                logger.info("Creating new vendor assistant for %s on Vapi", v_id)
                created_v = await vapi_client.create_assistant(vendor_payload)
                vendor_data = {
                    "id": created_v["id"],
                    "vendor_id": v_id,
                    "name": created_v.get("name", vendor_payload["name"]),
                    "payload": vendor_payload,
                }

            await store.set_vendor_assistant(v_id, vendor_data)
            results["vendors"][v_id] = vendor_data
        except Exception as e:
            logger.error("Failed to register vendor assistant %s: %s", v_id, e)
            results["vendors"][v_id] = {"error": str(e)}

    return results
