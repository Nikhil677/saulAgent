"""Launch Round Script for Vapi Negotiator.

Step 2: Places negotiation calls from the command line across vendors,
tracks call progress, and calculates competitive scorecards.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.call_manager import CallManager
from app.config import settings
from app.models import CallStatus, NegotiationOutcome
from app.store import Store
from app.vapi_client import VapiClient


def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


async def main():
    parser = argparse.ArgumentParser(description="Launch a competitive negotiation round across vendors.")
    parser.add_argument("--vendor", type=str, help="Target specific vendor ID (optional)")
    parser.add_argument("--simulated", action="store_true", help="Run simulated calls without outbound telephony")
    parser.add_argument("--concurrency", type=int, default=1, help="Number of concurrent calls (default: 1)")
    args = parser.parse_args()

    print("=" * 70)
    print("  VAPI NEGOTIATOR - STEP 2: LAUNCH COMPETITIVE ROUND")
    print("=" * 70)

    store = Store(settings.store_file_path)
    brief = load_json(settings.brief_file_path)
    vendors = load_json(settings.vendors_file_path)

    # Check if bootstrap has been run
    assistants = await store.get_all_assistants()
    if not assistants.get("buyer") and not args.simulated:
        print("\n[!] Buyer assistant not found in local store.")
        print("    Running in simulated mode. (Run 'python scripts/bootstrap.py' first for live calls)")
        args.simulated = True

    client = VapiClient(api_key=settings.vapi_api_key, base_url=settings.vapi_base_url)
    manager = CallManager(
        vapi_client=client,
        store=store,
        brief=brief,
        vendors=vendors,
        phone_number_id=settings.vapi_phone_number_id,
    )

    vendor_ids = [args.vendor] if args.vendor else [v["id"] for v in vendors]

    print(f"\n[*] Procurement Target: {brief['item']}")
    print(f"[*] Target Price: ${brief['target_price_per_unit']:,.2f} | Walk-Away: ${brief['walk_away_price_per_unit']:,.2f}")
    print(f"[*] Mode: {'Simulated Call Pipeline' if args.simulated else 'Live Outbound Telephony'}")
    print(f"[*] Vendors in round: {', '.join(vendor_ids)}")

    try:
        round_record = await manager.launch_round(
            vendor_ids=vendor_ids,
            concurrency=args.concurrency,
            simulated=args.simulated,
        )

        print("\n[+] Round initialized successfully!")
        print(f"    Round ID:  {round_record.round_id}")
        print(f"    Call IDs:  {', '.join(round_record.call_ids)}")

        # If simulated, let's inject realistic simulated negotiation exchanges for each call
        if args.simulated:
            print("\n[*] Simulating conversation turns for each vendor...")
            simulated_outcomes = [
                {"price": 1280.0, "summary": "Dave Miller agreed to $1,280/unit after 50-unit volume commitment and waived freight."},
                {"price": 1390.0, "summary": "Elena Rostova held firm at $1,390/unit, citing ISO certifications and 5-year warranty."},
                {"price": 1190.0, "summary": "Marcus Chen closed aggressively at $1,190/unit with 30% upfront deposit."},
            ]

            for i, call_id in enumerate(round_record.call_ids):
                sim_outcome = simulated_outcomes[i % len(simulated_outcomes)]
                vid = vendor_ids[i]
                vinfo = next((v for v in vendors if v["id"] == vid), {})

                call = await store.get_call(call_id)
                if call:
                    call.status = CallStatus.ENDED
                    call.final_offer_price = sim_outcome["price"]
                    call.outcome = (
                        NegotiationOutcome.DEAL_ACCEPTED
                        if sim_outcome["price"] <= brief["walk_away_price_per_unit"]
                        else NegotiationOutcome.WALK_AWAY
                    )
                    call.summary = sim_outcome["summary"]
                    call.duration_seconds = 142.0
                    call.cost = 0.08
                    call.deal_terms = {
                        "unit_price": sim_outcome["price"],
                        "total_contract_value": sim_outcome["price"] * brief["quantity"],
                        "savings_vs_list": (vinfo.get("list_price", 1500) - sim_outcome["price"]) * brief["quantity"],
                    }
                    await store.upsert_call(call)

            # Re-evaluate round scorecard
            await manager._update_round_scorecard(round_record.round_id)
            updated_round = await store.get_round(round_record.round_id)
            round_record = updated_round or round_record

        print("\n" + "=" * 70)
        print("  ROUND SCORECARD & OUTCOMES")
        print("=" * 70)

        for cid in round_record.call_ids:
            c = await store.get_call(cid)
            if c:
                price_display = f"${c.final_offer_price:,.2f}" if c.final_offer_price else "N/A"
                print(f"\n  Vendor:   {c.vendor_name} ({c.vendor_id})")
                print(f"  Call ID:  {c.call_id}")
                print(f"  Status:   {c.status.value.upper()} | Outcome: {c.outcome.value.upper()}")
                print(f"  Agreed:   {price_display} per unit")
                if c.summary:
                    print(f"  Notes:    {c.summary}")

        print("\n" + "-" * 70)
        if round_record.winning_vendor_name:
            print(f"  🏆 WINNING VENDOR:    {round_record.winning_vendor_name} ({round_record.winning_vendor_id})")
            print(f"  💰 BEST AGREED PRICE:  ${round_record.best_price:,.2f} per unit")
            if round_record.total_savings:
                print(f"  💵 TOTAL SAVINGS:     ${round_record.total_savings:,.2f} (vs mandate ceiling)")
        else:
            print("  Round status: in progress. Check webhook logs or run again once calls finish.")
        print("=" * 70)

    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
