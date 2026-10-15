"""Bootstrap Script for Vapi Negotiator.

Step 1: Creates and registers the Buyer Assistant and all simulated Vendor Assistants on Vapi.ai,
queries available phone numbers, and commits the state to data/store.json.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.assistants import register_all_assistants
from app.config import settings
from app.store import Store
from app.vapi_client import VapiClient


def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


async def main():
    print("=" * 70)
    print("  VAPI NEGOTIATOR - STEP 1: BOOTSTRAP ASSISTANTS & NUMBERS")
    print("=" * 70)

    if not settings.vapi_api_key or settings.vapi_api_key == "your_private_vapi_api_key_here":
        print("\n[!] WARNING: VAPI_API_KEY is not set in your .env file.")
        print("    Please set VAPI_API_KEY to your Vapi private API key to register live assistants.")
        print("    Running in dry-run/mock mode for demonstration...\n")

        # Mock bootstrap for offline demonstration
        store = Store(settings.store_file_path)
        brief = load_json(settings.brief_file_path)
        vendors = load_json(settings.vendors_file_path)

        mock_buyer = {
            "id": "asst_buyer_mock_001",
            "name": f"Buyer Agent - Alex Mercer ({brief['item'][:25]})",
            "mock": True,
        }
        await store.set_buyer_assistant(mock_buyer)

        for v in vendors:
            mock_vendor = {
                "id": f"asst_vendor_{v['id']}_mock",
                "vendor_id": v["id"],
                "name": f"Vendor Agent - {v['name']} ({v['contact_name']})",
                "mock": True,
            }
            await store.set_vendor_assistant(v["id"], mock_vendor)

        print("[+] Mock Buyer Assistant saved to store:", mock_buyer["id"])
        for v in vendors:
            print(f"[+] Mock Vendor Assistant for {v['name']} saved to store.")

        print("\n[OK] Mock bootstrap completed. You can run simulate_webhook.py or web_call_demo.html.")
        return

    # Real Vapi API bootstrap
    store = Store(settings.store_file_path)
    client = VapiClient(api_key=settings.vapi_api_key, base_url=settings.vapi_base_url)
    brief = load_json(settings.brief_file_path)
    vendors = load_json(settings.vendors_file_path)

    print(f"\n[*] Loaded Brief: {brief.get('item')} ({brief.get('quantity')} units)")
    print(f"[*] Target Price: ${brief.get('target_price_per_unit')} | Walk-Away: ${brief.get('walk_away_price_per_unit')}")
    print(f"[*] Registering {len(vendors)} vendor personas and 1 buyer assistant on Vapi...")

    try:
        registration = await register_all_assistants(
            vapi_client=client,
            store=store,
            brief=brief,
            vendors=vendors,
            webhook_url=settings.webhook_url,
            webhook_secret=settings.webhook_secret,
            provider=settings.default_model_provider,
            model=settings.default_model,
        )

        print("\n" + "-" * 70)
        print("  REGISTERED ASSISTANTS SUMMARY")
        print("-" * 70)
        buyer = registration.get("buyer")
        if buyer and "id" in buyer:
            print(f"  [BUYER]  ID: {buyer['id']}")
            print(f"           Name: {buyer['name']}")
        elif buyer and "error" in buyer:
            print(f"  [BUYER ERROR] {buyer['error']}")

        print("\n  [VENDORS]")
        for vid, vdata in registration.get("vendors", {}).items():
            if "id" in vdata:
                print(f"  - {vid:<25} ID: {vdata['id']} ({vdata['name']})")
            else:
                print(f"  - {vid:<25} ERROR: {vdata.get('error')}")

        # Check Phone Numbers
        print("\n[*] Checking provisioned phone numbers on Vapi...")
        try:
            numbers = await client.list_phone_numbers()
            if numbers:
                print(f"[+] Found {len(numbers)} phone numbers on Vapi account:")
                for num in numbers:
                    num_id = num.get("id")
                    num_val = num.get("number", "Unknown")
                    print(f"    - {num_val} (ID: {num_id})")
                    await store.set_phone_number(num_id, num)
            else:
                print("[-] No phone numbers found on Vapi. (Web call demo can be used without numbers).")
        except Exception as ne:
            print(f"[-] Could not list phone numbers: {ne}")

        print("\n" + "=" * 70)
        print("  BOOTSTRAP COMPLETE! State persisted to data/store.json")
        print("  Next steps:")
        print("    1. Start server:    uvicorn app.main:app --reload")
        print("    2. Open Web Demo:   open web_call_demo.html or http://localhost:8000/demo")
        print("    3. Launch Round:    python scripts/launch_round.py")
        print("=" * 70)

    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
