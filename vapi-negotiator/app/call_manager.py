"""Launches calls, tracks their lifecycle, and ingests Vapi events.

Event sources (all funnel into the same CallRecord):
  * webhook: status-update, transcript, end-of-call-report   (see webhook.py)
  * polling: GET /call/{id}  via sync_from_api()             (fallback if a webhook is missed)
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import uuid
from datetime import datetime
from typing import Any, Awaitable, Callable

from .config import Settings
from .models import CallRecord, CallStatus, Turn, utc_now_iso
from .store import JsonStore
from .vapi_client import VapiClient, VapiError

log = logging.getLogger("negotiator.calls")

CompletionHandler = Callable[[CallRecord], Awaitable[None] | None]

_STATUS_MAP = {
    "scheduled": CallStatus.QUEUED,
    "queued": CallStatus.QUEUED,
    "ringing": CallStatus.RINGING,
    "in-progress": CallStatus.IN_PROGRESS,
    "forwarding": CallStatus.IN_PROGRESS,
}


# ------------------------------------------------------------------- helpers
def load_json(path) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _role(raw: str | None) -> str | None:
    if raw in ("bot", "assistant", "agent"):
        return "agent"
    if raw in ("user", "customer"):
        return "vendor"
    return None  # system / tool messages are ignored


def normalize_messages(messages: list[dict]) -> list[Turn]:
    """artifact.messages -> ordered, timestamped turns (timestamps = evidence anchors)."""
    turns: list[Turn] = []
    for m in messages or []:
        role = _role(m.get("role"))
        text = (m.get("message") or m.get("content") or "").strip()
        if not role or not text:
            continue
        start = m.get("secondsFromStart")
        turns.append(Turn(role=role, text=text, start_sec=float(start) if start is not None else None))
    return turns


def classify(ended_reason: str | None, turns: list[Turn]) -> CallStatus:
    """Map Vapi's endedReason (+ whether the vendor ever spoke) to our outcome."""
    r = (ended_reason or "").lower()
    vendor_spoke = any(t.role == "vendor" for t in turns)
    if "voicemail" in r:
        return CallStatus.VOICEMAIL
    if any(k in r for k in ("did-not-answer", "no-answer", "busy", "rejected")):
        return CallStatus.NO_ANSWER
    if any(k in r for k in ("error", "failed", "fault", "invalid", "unreachable")):
        return CallStatus.FAILED
    if not vendor_spoke:
        return CallStatus.NO_ANSWER  # picked up but nobody said anything useful
    return CallStatus.COMPLETED


def _seconds_between(start: str | None, end: str | None) -> float | None:
    try:
        a = datetime.fromisoformat(start.replace("Z", "+00:00"))
        b = datetime.fromisoformat(end.replace("Z", "+00:00"))
        return round((b - a).total_seconds(), 1)
    except (AttributeError, ValueError):
        return None


# ------------------------------------------------------------------- manager
class CallManager:
    def __init__(self, client: VapiClient | None, settings: Settings,
                 state_store: JsonStore, calls_store: JsonStore,
                 vendors: list[dict], brief: dict):
        self.client = client
        self.settings = settings
        self.state_store = state_store      # ids of assistants / numbers (from bootstrap)
        self.calls_store = calls_store      # persisted CallRecords
        self.vendors = {v["id"]: v for v in vendors}
        self.brief = brief
        self.records: dict[str, CallRecord] = {}
        self._by_call_id: dict[str, str] = {}
        self._handlers: list[CompletionHandler] = []
        self._notified: set[str] = set()
        self._load()

    # --------------------------------------------------------- persistence
    def _load(self) -> None:
        for rid, raw in self.calls_store.load().items():
            try:
                rec = CallRecord.model_validate(raw)
            except Exception:  # corrupt entry: skip it
                continue
            self.records[rid] = rec
            if rec.call_id:
                self._by_call_id[rec.call_id] = rid
            if rec.is_terminal:
                self._notified.add(rid)  # do not re-fire handlers after a restart

    def _persist(self) -> None:
        self.calls_store.save({rid: r.model_dump(mode="json") for rid, r in self.records.items()})

    # ------------------------------------------------------------ handlers
    def add_completion_handler(self, fn: CompletionHandler) -> None:
        """Register a function called ONCE when a call reaches a terminal status.
        Your extraction + scoring code plugs in here."""
        self._handlers.append(fn)

    async def dispatch_completion(self, rec: CallRecord) -> None:
        if not rec.is_terminal or rec.record_id in self._notified:
            return
        self._notified.add(rec.record_id)
        for fn in self._handlers:
            try:
                out = fn(rec)
                if inspect.isawaitable(out):
                    await out
            except Exception:
                log.exception("completion handler failed for %s", rec.record_id)

    # -------------------------------------------------------------- lookup
    def get(self, key: str) -> CallRecord | None:
        return self.records.get(key) or self.records.get(self._by_call_id.get(key, ""))

    def _resolve(self, call: dict) -> CallRecord | None:
        """Find (or rebuild) the record for an incoming event.

        Events can arrive BEFORE POST /call returns, so we also match on the record_id we put
        into the call's metadata, and we can rebuild a record after a server restart."""
        call_id = call.get("id")
        meta = call.get("metadata") or {}
        rec = self.get(call_id) if call_id else None
        if rec is None and meta.get("record_id"):
            rec = self.records.get(meta["record_id"])
        if rec is None and meta.get("vendor_id"):
            vid = meta["vendor_id"]
            rec = CallRecord(
                record_id=meta.get("record_id") or f"rec_{uuid.uuid4().hex[:10]}",
                vendor_id=vid,
                vendor_name=self.vendors.get(vid, {}).get("name", vid),
                round=int(meta.get("round", 1)),
            )
            self.records[rec.record_id] = rec
        if rec is None:
            return None  # a call that is not ours
        if call_id and rec.call_id != call_id:
            rec.call_id = call_id
            self._by_call_id[call_id] = rec.record_id
        return rec

    # ----------------------------------------------------- build + launch
    def vendor_phone(self, vendor: dict) -> str:
        if vendor.get("mode", "simulated") == "simulated":
            number = self.state_store.load().get("vendors", {}).get(vendor["id"], {}).get("number")
            if not number:
                raise ValueError(f"No number for simulated vendor '{vendor['id']}'. Run scripts/bootstrap.py first.")
            return number
        if not vendor.get("phone"):
            raise ValueError(f"Vendor '{vendor['id']}' is mode=real but has no 'phone' in vendors.json")
        return vendor["phone"]

    def build_call_payload(self, vendor: dict, round_no: int, record_id: str,
                           competing_quote: str | None = None,
                           earliest_at: str | None = None) -> dict:
        state = self.state_store.load()
        if not state.get("buyer_assistant_id") or not state.get("buyer_phone_number_id"):
            raise ValueError("Missing buyer assistant/number. Run scripts/bootstrap.py first.")
        b = self.brief
        variables = {  # Vapi templating needs string values
            "vendor_name": vendor["name"],
            "buyer_org": b["buyer_org"],
            "item_description": b["item_description"],
            "quantity": str(b["quantity"]),
            "target_price": str(b["target_unit_price"]),
            "walk_away_price": str(b["walk_away_unit_price"]),
            "must_haves": "; ".join(b.get("must_haves", [])) or "none",
            "deadline": b.get("deadline", "as soon as possible"),
            "round_number": str(round_no),
            "competing_quote": competing_quote or "none",
        }
        payload: dict[str, Any] = {
            "assistantId": state["buyer_assistant_id"],
            "phoneNumberId": state["buyer_phone_number_id"],
            "customer": {"number": self.vendor_phone(vendor), "name": vendor["name"]},
            "assistantOverrides": {"variableValues": variables},
            "metadata": {"record_id": record_id, "vendor_id": vendor["id"], "round": round_no},
        }
        if earliest_at:
            payload["schedulePlan"] = {"earliestAt": earliest_at}  # ISO-8601
        return payload

    async def launch_call(self, vendor_id: str, round_no: int = 1,
                          competing_quote: str | None = None,
                          earliest_at: str | None = None) -> CallRecord:
        if self.client is None:
            raise VapiError(0, "VAPI_API_KEY is not configured")
        vendor = self.vendors[vendor_id]
        rec = CallRecord(record_id=f"rec_{uuid.uuid4().hex[:10]}", vendor_id=vendor_id,
                         vendor_name=vendor["name"], round=round_no,
                         competing_quote_used=competing_quote)
        self.records[rec.record_id] = rec
        try:
            payload = self.build_call_payload(vendor, round_no, rec.record_id, competing_quote, earliest_at)
            resp = await self.client.create_call(payload)
            rec.call_id = resp.get("id")
            if rec.call_id:
                self._by_call_id[rec.call_id] = rec.record_id
        except (VapiError, ValueError) as exc:
            rec.status, rec.error, rec.ended_at = CallStatus.FAILED, str(exc), utc_now_iso()
            log.error("launch failed for %s: %s", vendor_id, exc)
        self._persist()
        if rec.is_terminal:
            await self.dispatch_completion(rec)
        return rec

    async def launch_round(self, round_no: int = 1, vendor_ids: list[str] | None = None,
                           leverage: dict[str, str] | None = None,
                           concurrency: int = 3) -> list[CallRecord]:
        """Launch calls to several vendors in parallel (bounded by `concurrency`).

        leverage maps vendor_id -> the competing-quote sentence the agent may use, e.g.
        {"printco": "another vendor offered 430 rupees per shirt with 7 day delivery"}.
        Only pass REAL quotes you actually collected."""
        ids = vendor_ids or list(self.vendors)
        sem = asyncio.Semaphore(concurrency)

        async def one(vid: str) -> CallRecord:
            async with sem:
                return await self.launch_call(vid, round_no, (leverage or {}).get(vid))

        return list(await asyncio.gather(*(one(v) for v in ids)))

    # ------------------------------------------------------- event ingest
    def on_status_update(self, msg: dict) -> CallRecord | None:
        rec = self._resolve(msg.get("call") or {})
        if rec is None:
            return None
        raw = msg.get("status")
        if raw in _STATUS_MAP and not rec.is_terminal:
            rec.status = _STATUS_MAP[raw]
        if raw == "in-progress" and not rec.started_at:
            rec.started_at = utc_now_iso()
        if raw == "ended" and msg.get("endedReason"):
            rec.ended_reason = msg["endedReason"]
        self._persist()
        return rec

    def on_transcript(self, msg: dict) -> CallRecord | None:
        if msg.get("transcriptType", "final") != "final":
            return None  # ignore partials
        rec = self._resolve(msg.get("call") or {})
        role, text = _role(msg.get("role")), (msg.get("transcript") or "").strip()
        if rec is None or not role or not text:
            return rec
        rec.live_turns.append(Turn(role=role, text=text))
        self._persist()
        return rec

    def on_end_of_call_report(self, msg: dict) -> CallRecord | None:
        rec = self._resolve(msg.get("call") or {})
        if rec is None:
            return None
        self._ingest(rec, call=msg.get("call") or {}, artifact=msg.get("artifact") or {},
                     analysis=msg.get("analysis") or {}, extra=msg)
        return rec

    def _ingest(self, rec: CallRecord, call: dict, artifact: dict, analysis: dict, extra: dict) -> None:
        def pick(key: str):
            return extra.get(key) if extra.get(key) is not None else call.get(key)

        rec.ended_reason = pick("endedReason") or rec.ended_reason
        rec.started_at = pick("startedAt") or rec.started_at
        rec.ended_at = pick("endedAt") or rec.ended_at or utc_now_iso()
        rec.cost = pick("cost") if pick("cost") is not None else rec.cost
        rec.duration_sec = pick("durationSeconds") or _seconds_between(rec.started_at, rec.ended_at) or rec.duration_sec
        rec.summary = analysis.get("summary") or extra.get("summary") or rec.summary
        rec.recording_url = artifact.get("recordingUrl") or extra.get("recordingUrl") or rec.recording_url
        rec.transcript = artifact.get("transcript") or extra.get("transcript") or rec.transcript
        turns = normalize_messages(artifact.get("messages") or extra.get("messages") or [])
        rec.turns = turns or rec.turns or rec.live_turns
        rec.status = classify(rec.ended_reason, rec.turns)
        self._persist()

    # ------------------------------------------------- polling (fallback)
    async def sync_from_api(self, key: str) -> CallRecord | None:
        """Pull GET /call/{id}. Use when a webhook was missed or no tunnel is available."""
        rec = self.get(key)
        if rec is None or not rec.call_id or self.client is None:
            return rec
        call = await self.client.get_call(rec.call_id)
        raw = call.get("status")
        if raw == "ended":
            self._ingest(rec, call=call, artifact=call.get("artifact") or {},
                         analysis=call.get("analysis") or {}, extra={})
            await self.dispatch_completion(rec)
        elif raw in _STATUS_MAP:
            rec.status = _STATUS_MAP[raw]
            self._persist()
        return rec

    async def wait_until_done(self, records: list[CallRecord], interval: float = 3.0,
                              timeout: float = 600.0) -> list[CallRecord]:
        """Poll until every record is terminal or the timeout passes (used by CLI scripts)."""
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            pending = [r for r in records if not r.is_terminal]
            if not pending:
                break
            await asyncio.gather(*(self.sync_from_api(r.record_id) for r in pending),
                                 return_exceptions=True)
            await asyncio.sleep(interval)
        return records