"""Webhook Simulation Script for Vapi Negotiator.

Simulates a complete real-world Vapi.ai call lifecycle (ringing -> in-progress ->
conversation turns -> end-of-call-report) to verify the webhook receiver and
outcome classification without needing an actual phone call or active Vapi balance.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import sys
import uuid
from pathlib import Path
import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings
from app.main import app
from app.models import CallStatus
from app.store import Store


async def send_event(client: httpx.AsyncClient, webhook_url: str, event_payload: dict):
    headers = {"Content-Type": "application/json"}
    if settings.webhook_secret:
        headers["x-vapi-secret"] = settings.webhook_secret

    response = await client.post(webhook_url, json=event_payload, headers=headers)
    print(f"  -> [{event_payload.get('message', {}).get('type')}] HTTP {response.status_code}: {response.text}")
    return response


async def main():
    print("=" * 70)
    print("  VAPI NEGOTIATOR - SIMULATE WEBHOOK CALL LIFECYCLE")
    print("=" * 70)

    call_id = f"sim-call-{uuid.uuid4().hex[:10]}"
    vendor_id = "vendor-apex-logistics"
    print(f"\n[*] Simulated Call ID: {call_id}")
    print(f"[*] Target Vendor:     Apex Industrial Supply ({vendor_id})")

    # Pre-seed the call in store
    store = Store(settings.store_file_path)
    from app.models import CallRecord
    call_record = CallRecord(
        call_id=call_id,
        vendor_id=vendor_id,
        vendor_name="Apex Industrial Supply",
        assistant_id="asst_buyer_alex_mercer",
        customer_number="+15550101001",
        status=CallStatus.QUEUED,
        started_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )
    await store.upsert_call(call_record)

    # Determine whether server is running on localhost:8000 or use in-process ASGI
    target_url = f"http://localhost:{settings.server_port}/vapi/webhook"
    use_in_process = False

    async with httpx.AsyncClient() as probe_client:
        try:
            r = await probe_client.get(f"http://localhost:{settings.server_port}/", timeout=1.0)
            print(f"[+] Connected to live server on http://localhost:{settings.server_port}")
        except Exception:
            print(f"[-] Server not detected on port {settings.server_port}. Using in-process ASGI transport.")
            use_in_process = True

    transport_client = (
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
        if use_in_process
        else httpx.AsyncClient()
    )
    post_url = "/vapi/webhook" if use_in_process else target_url

    async with transport_client as client:
        # Step 1: Status Update -> Ringing
        print("\n[Step 1] Sending status-update: RINGING...")
        await send_event(client, post_url, {
            "message": {
                "type": "status-update",
                "status": "ringing",
                "callId": call_id,
                "call": {"id": call_id},
            }
        })
        await asyncio.sleep(0.5)

        # Step 2: Status Update -> In-Progress
        print("\n[Step 2] Sending status-update: IN-PROGRESS...")
        await send_event(client, post_url, {
            "message": {
                "type": "status-update",
                "status": "in-progress",
                "callId": call_id,
                "call": {"id": call_id},
            }
        })
        await asyncio.sleep(0.5)

        # Step 3: Conversation Turns (Buyer & Vendor Exchanges)
        print("\n[Step 3] Sending conversation transcripts...")
        turns = [
            ("assistant", "Hello, this is Alex Mercer calling from Nexor Infrastructure regarding 50 units of 42U server enclosures. What is your volume pricing for this order?"),
            ("user", "Hey Alex! Dave Miller here at Apex. Our standard list price is $1,650, but for 50 units I could quote you $1,480 per unit."),
            ("assistant", "Thanks Dave, but we have competitive quotes and our procurement target is $1,200 per unit. Can you come closer to that number?"),
            ("user", "Look partner, aluminum costs are crazy right now. The lowest I could possibly do is $1,280, and I'll even throw in free freight if we close today."),
            ("assistant", "Dave, if you waive freight and commit to delivery within 4 weeks, we have a deal at $1,280 per unit for 50 units. Send over the invoice."),
            ("user", "You've got a deal Alex! I'll email the contract right away. Have a great day."),
        ]

        for role, text in turns:
            await send_event(client, post_url, {
                "message": {
                    "type": "transcript",
                    "role": role,
                    "transcript": text,
                    "callId": call_id,
                    "call": {"id": call_id},
                }
            })
            await asyncio.sleep(0.3)

        # Step 4: End-of-Call Report
        print("\n[Step 4] Sending end-of-call-report with structured analysis...")
        await send_event(client, post_url, {
            "message": {
                "type": "end-of-call-report",
                "callId": call_id,
                "call": {"id": call_id},
                "durationSeconds": 145.2,
                "cost": 0.084,
                "recordingUrl": "https://api.vapi.ai/mock/recording.wav",
                "summary": "Buyer negotiated Dave Miller from $1,650 down to $1,280/unit for 50 units with bundled free freight.",
                "analysis": {
                    "structuredData": {
                        "deal_reached": True,
                        "final_unit_price": 1280.0,
                        "agreed_quantity": 50,
                        "total_contract_value": 64000.0,
                        "agreed_delivery": "Within 4 weeks",
                        "agreed_payment_terms": "Net 30",
                        "outcome_summary": "Deal confirmed at $1,280 with waived freight.",
                    }
                },
            }
        })

    # Verify Final Call Record in Store
    print("\n" + "=" * 70)
    print("  VERIFYING RECORD IN STORE.JSON")
    print("=" * 70)
    final_record = await store.get_call(call_id)
    if final_record:
        print(f"  Call ID:      {final_record.call_id}")
        print(f"  Status:       {final_record.status.value}")
        print(f"  Outcome:      {final_record.outcome.value}")
        print(f"  Final Price:  ${final_record.final_offer_price:,.2f} per unit")
        print(f"  Turns Saved:  {len(final_record.turns)}")
        if final_record.deal_terms:
            print(f"  Savings:      ${final_record.deal_terms.get('savings_vs_list', 0):,.2f} vs list price")
        print(f"  Summary:      {final_record.summary}")
        print("\n[SUCCESS] Webhook simulation completed and validated!")
    else:
        print("\n[ERROR] Record could not be retrieved from store.")


if __name__ == "__main__":
    asyncio.run(main())
