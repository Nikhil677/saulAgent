"""Call Manager for Vapi Negotiator.

Coordinates outbound negotiation calls, tracks conversation rounds, processes
real-time webhook events, and classifies negotiation outcomes and scorecards.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import re
import uuid
from typing import Any, Dict, List, Optional

from app.models import (
    CallRecord,
    CallStatus,
    NegotiationOutcome,
    RoundRecord,
    Turn,
)
from app.store import Store
from app.vapi_client import VapiClient

logger = logging.getLogger(__name__)


class CallManager:
    """Orchestrates calls, round evaluations, and webhook event state updates."""

    def __init__(
        self,
        vapi_client: VapiClient,
        store: Store,
        brief: Dict[str, Any],
        vendors: List[Dict[str, Any]],
        phone_number_id: Optional[str] = None,
    ):
        self.vapi_client = vapi_client
        self.store = store
        self.brief = brief
        self.vendors_by_id = {v["id"]: v for v in vendors}
        self.phone_number_id = phone_number_id

    # -------------------------------------------------------------------------
    # Launch Single Call
    # -------------------------------------------------------------------------
    async def launch_call(
        self,
        vendor_id: str,
        phone_number: Optional[str] = None,
        round_id: Optional[str] = None,
        simulated: bool = False,
    ) -> CallRecord:
        """Initiate a negotiation call to a specific vendor."""
        vendor = self.vendors_by_id.get(vendor_id)
        if not vendor:
            raise ValueError(f"Vendor '{vendor_id}' not found in registered vendors.")

        buyer_assistant = await self.store.get_buyer_assistant()
        buyer_assistant_id = buyer_assistant.get("id") if buyer_assistant else "mock-buyer-assistant"

        target_phone = phone_number or vendor.get("phone_number") or "+15550000000"
        call_id = f"call-{uuid.uuid4().hex[:12]}"

        call_record = CallRecord(
            call_id=call_id,
            round_id=round_id,
            vendor_id=vendor_id,
            vendor_name=vendor.get("name", "Unknown Vendor"),
            assistant_id=buyer_assistant_id,
            phone_number_id=self.phone_number_id,
            customer_number=target_phone,
            status=CallStatus.QUEUED,
            started_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        )

        if simulated:
            logger.info("Launching simulated call %s for vendor %s", call_id, vendor_id)
            call_record.status = CallStatus.IN_PROGRESS
            await self.store.upsert_call(call_record)
            return call_record

        # Real Vapi call initiation
        try:
            extra_payload = {
                "metadata": {
                    "round_id": round_id,
                    "vendor_id": vendor_id,
                    "brief_id": self.brief.get("brief_id"),
                }
            }
            vapi_response = await self.vapi_client.create_call(
                assistant_id=buyer_assistant_id,
                phone_number_id=self.phone_number_id,
                customer_number=target_phone,
                extra_payload=extra_payload,
            )
            vapi_call_id = vapi_response.get("id", call_id)
            call_record.call_id = vapi_call_id
            call_record.status = CallStatus.RINGING
            call_record.raw_metadata = vapi_response
            await self.store.upsert_call(call_record)
            return call_record
        except Exception as e:
            logger.error("Failed to launch Vapi call for %s: %s", vendor_id, e)
            call_record.status = CallStatus.FAILED
            call_record.summary = f"Launch error: {str(e)}"
            await self.store.upsert_call(call_record)
            raise

    # -------------------------------------------------------------------------
    # Launch Round
    # -------------------------------------------------------------------------
    async def launch_round(
        self,
        vendor_ids: Optional[List[str]] = None,
        concurrency: int = 1,
        simulated: bool = False,
    ) -> RoundRecord:
        """Launch a competitive negotiation round across multiple vendors."""
        target_vendors = vendor_ids or list(self.vendors_by_id.keys())
        round_id = f"round-{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S')}"

        round_record = RoundRecord(
            round_id=round_id,
            created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            brief_id=self.brief.get("brief_id", "brief-default"),
            target_price=self.brief.get("target_price_per_unit", 1200.0),
            walk_away_price=self.brief.get("walk_away_price_per_unit", 1450.0),
            call_ids=[],
            status="in_progress",
        )
        await self.store.upsert_round(round_record)

        call_records = []
        if concurrency > 1:
            # Parallel launches
            tasks = [
                self.launch_call(vid, round_id=round_id, simulated=simulated)
                for vid in target_vendors
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for res in results:
                if isinstance(res, CallRecord):
                    call_records.append(res)
                    round_record.call_ids.append(res.call_id)
        else:
            # Sequential launches
            for vid in target_vendors:
                try:
                    c = await self.launch_call(vid, round_id=round_id, simulated=simulated)
                    call_records.append(c)
                    round_record.call_ids.append(c.call_id)
                except Exception as e:
                    logger.error("Error launching call for vendor %s in round %s: %s", vid, round_id, e)

        await self.store.upsert_round(round_record)
        return round_record

    # -------------------------------------------------------------------------
    # Webhook Event Processing
    # -------------------------------------------------------------------------
    async def handle_webhook_event(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Process incoming webhook payloads from Vapi."""
        message = payload.get("message", payload)
        message_type = message.get("type", "unknown")
        call_data = message.get("call", {})
        call_id = call_data.get("id") or message.get("callId")

        if not call_id:
            logger.warning("Received webhook event without call ID: %s", message_type)
            return {"status": "ignored", "reason": "missing_call_id"}

        call_record = await self.store.get_call(call_id)
        if not call_record:
            # Initialize placeholder record if not present
            call_record = CallRecord(
                call_id=call_id,
                vendor_id=message.get("metadata", {}).get("vendor_id", "unknown-vendor"),
                vendor_name="Unknown Vendor",
                assistant_id=message.get("assistantId", "unknown-assistant"),
                status=CallStatus.IN_PROGRESS,
            )

        # Route by event type
        if message_type == "status-update":
            status_str = message.get("status", "").lower()
            if status_str in [s.value for s in CallStatus]:
                call_record.status = CallStatus(status_str)
            if status_str == "in-progress" and not call_record.started_at:
                call_record.started_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

        elif message_type in ("transcript", "speech-update"):
            role = message.get("role", "assistant")
            transcript_text = message.get("transcript", message.get("text", "")).strip()
            if transcript_text:
                turn = Turn(
                    role=role,
                    message=transcript_text,
                    timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    extracted_price=self._extract_price_from_text(transcript_text),
                )
                call_record.turns.append(turn)

        elif message_type == "end-of-call-report":
            call_record.status = CallStatus.ENDED
            call_record.ended_at = message.get("endedAt") or datetime.datetime.now(datetime.timezone.utc).isoformat()
            call_record.duration_seconds = message.get("durationSeconds") or message.get("duration")
            call_record.cost = message.get("cost")
            call_record.recording_url = message.get("recordingUrl")
            call_record.summary = message.get("summary")
            call_record.transcript = message.get("transcript")

            # Extract structured analysis
            analysis = message.get("analysis", {})
            structured_data = analysis.get("structuredData") or {}
            call_record.analysis = analysis

            # Classify outcome and pricing
            self._classify_call_outcome(call_record, structured_data)

            # Update associated round if present
            if call_record.round_id:
                await self._update_round_scorecard(call_record.round_id)

        await self.store.upsert_call(call_record)
        return {"status": "success", "call_id": call_id, "type": message_type}

    # -------------------------------------------------------------------------
    # Outcome Classification & Pricing Extraction
    # -------------------------------------------------------------------------
    def _extract_price_from_text(self, text: str) -> Optional[float]:
        """Regex helper to extract dollar prices from conversation text."""
        # Matches patterns like $1,250 or $1250 or $1250.00
        matches = re.findall(r"\$\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})?)", text)
        if matches:
            try:
                cleaned = matches[-1].replace(",", "")
                return float(cleaned)
            except ValueError:
                pass
        return None

    def _classify_call_outcome(
        self,
        call_record: CallRecord,
        structured_data: Dict[str, Any],
    ) -> None:
        """Determine whether the negotiation was successful and calculate savings."""
        final_price = structured_data.get("final_unit_price")
        deal_reached = structured_data.get("deal_reached", False)

        # Fallback price extraction from transcript turns if structuredData missed it
        if final_price is None and call_record.turns:
            prices = [t.extracted_price for t in call_record.turns if t.extracted_price is not None]
            if prices:
                final_price = prices[-1]

        target_price = self.brief.get("target_price_per_unit", 1200.0)
        walk_away_price = self.brief.get("walk_away_price_per_unit", 1450.0)
        quantity = self.brief.get("quantity", 50)
        vendor = self.vendors_by_id.get(call_record.vendor_id, {})
        list_price = vendor.get("list_price", target_price * 1.3)

        call_record.final_offer_price = final_price

        if deal_reached or (final_price and final_price <= walk_away_price):
            call_record.outcome = NegotiationOutcome.DEAL_ACCEPTED
            savings_per_unit = list_price - (final_price or target_price)
            call_record.deal_terms = {
                "unit_price": final_price,
                "total_contract_value": (final_price or target_price) * quantity,
                "savings_vs_list": savings_per_unit * quantity,
                "status": "Accepted within mandate",
            }
        elif final_price and final_price > walk_away_price:
            call_record.outcome = NegotiationOutcome.WALK_AWAY
        else:
            call_record.outcome = NegotiationOutcome.DEAL_REJECTED

    async def _update_round_scorecard(self, round_id: str) -> None:
        """Recalculate winning vendor and total savings for a round."""
        round_record = await self.store.get_round(round_id)
        if not round_record:
            return

        all_calls = [
            await self.store.get_call(cid) for cid in round_record.call_ids
        ]
        completed_calls = [c for c in all_calls if c and c.status == CallStatus.ENDED]

        accepted_calls = [
            c for c in completed_calls
            if c.outcome == NegotiationOutcome.DEAL_ACCEPTED and c.final_offer_price is not None
        ]

        if accepted_calls:
            # Sort by lowest agreed price
            accepted_calls.sort(key=lambda c: c.final_offer_price or float("inf"))
            winner = accepted_calls[0]
            round_record.winning_vendor_id = winner.vendor_id
            round_record.winning_vendor_name = winner.vendor_name
            round_record.best_price = winner.final_offer_price

            quantity = self.brief.get("quantity", 50)
            target_price = self.brief.get("target_price_per_unit", 1200.0)
            # Savings calculated against target budget ceiling
            round_record.total_savings = (self.brief.get("walk_away_price_per_unit", 1450.0) - (winner.final_offer_price or target_price)) * quantity

        if len(completed_calls) == len(round_record.call_ids) and len(round_record.call_ids) > 0:
            round_record.status = "completed"

        await self.store.upsert_round(round_record)
