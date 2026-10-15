"""Offline tests: no network, no Vapi account, no cost.  Run: pytest -q"""
import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.call_manager import CallManager, classify, normalize_messages
from app.config import Settings
from app.main import build_manager, create_app
from app.models import CallStatus, Turn
from app.store import JsonStore

ROOT = Path(__file__).resolve().parent.parent
CALL = {"id": "call_abc", "metadata": {"record_id": "rec_1", "vendor_id": "printco", "round": 1}}
MESSAGES = [
    {"role": "bot", "message": "Hello, I'm an AI assistant.", "secondsFromStart": 0.5},
    {"role": "system", "message": "internal", "secondsFromStart": 0.6},
    {"role": "user", "message": "Four ninety per shirt.", "secondsFromStart": 7.25},
]


class FakeClient:
    """Stands in for VapiClient; records the payloads it is given."""
    def __init__(self):
        self.created = []

    async def create_call(self, payload):
        self.created.append(payload)
        return {"id": f"call_{len(self.created)}"}

    async def get_call(self, call_id):
        return {"id": call_id, "status": "ended", "endedReason": "customer-ended-call",
                "artifact": {"messages": MESSAGES, "transcript": "x"}, "analysis": {"summary": "s"}}


@pytest.fixture
def settings(tmp_path):
    return Settings(state_dir=tmp_path, config_dir=ROOT / "config", webhook_secret="s3cret")


@pytest.fixture
def seeded(settings):
    JsonStore(settings.state_dir / "state.json").save({
        "buyer_assistant_id": "asst_buyer", "buyer_phone_number_id": "pn_buyer",
        "vendors": {v: {"number": f"+1415555000{i}"} for i, v in enumerate(["printco", "stitchhub", "quickprint"])},
    })
    return settings


@pytest.fixture
def http(seeded):
    return TestClient(create_app(seeded))


def post(http, msg, secret="s3cret"):
    return http.post("/vapi/webhook", json={"message": msg}, headers={"X-Vapi-Secret": secret})


# ------------------------------------------------------------------ webhook
def test_rejects_wrong_secret(http):
    assert post(http, {"type": "status-update"}, secret="nope").status_code == 401


def test_accepts_bearer_secret(http):
    r = http.post("/vapi/webhook", json={"message": {"type": "hang"}}, headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 200


def test_bad_json(http):
    r = http.post("/vapi/webhook", content="not json", headers={"X-Vapi-Secret": "s3cret"})
    assert r.status_code == 400


def test_full_event_sequence_builds_a_record(http):
    post(http, {"type": "status-update", "status": "ringing", "call": CALL})
    post(http, {"type": "status-update", "status": "in-progress", "call": CALL})
    post(http, {"type": "transcript", "transcriptType": "partial", "role": "user", "transcript": "fo", "call": CALL})
    post(http, {"type": "transcript", "transcriptType": "final", "role": "user", "transcript": "Four ninety.", "call": CALL})
    post(http, {"type": "end-of-call-report", "endedReason": "customer-ended-call", "call": CALL,
                "durationSeconds": 30, "cost": 0.2, "analysis": {"summary": "ok"},
                "artifact": {"messages": MESSAGES, "transcript": "t", "recordingUrl": "u"}})
    rec = http.get("/calls/call_abc").json()
    assert rec["status"] == "completed" and rec["vendor_id"] == "printco"
    assert [t["role"] for t in rec["turns"]] == ["agent", "vendor"]       # system line dropped
    assert rec["turns"][1]["start_sec"] == 7.25                            # evidence timestamp kept
    assert len(rec["live_turns"]) == 1                                     # partial ignored
    assert rec["summary"] == "ok" and rec["recording_url"] == "u"


def test_events_for_unknown_calls_are_ignored(http):
    assert post(http, {"type": "status-update", "status": "ringing", "call": {"id": "foreign"}}).status_code == 200
    assert http.get("/calls").json() == []


# ------------------------------------------------------------ pure functions
def test_classify():
    t_vendor = [Turn(role="vendor", text="hi")]
    assert classify("voicemail", []) == CallStatus.VOICEMAIL
    assert classify("customer-did-not-answer", []) == CallStatus.NO_ANSWER
    assert classify("customer-busy", []) == CallStatus.NO_ANSWER
    assert classify("pipeline-error-openai-llm-failed", t_vendor) == CallStatus.FAILED
    assert classify("customer-ended-call", t_vendor) == CallStatus.COMPLETED
    assert classify("silence-timed-out", []) == CallStatus.NO_ANSWER


def test_normalize_messages():
    turns = normalize_messages(MESSAGES)
    assert [(t.role, t.start_sec) for t in turns] == [("agent", 0.5), ("vendor", 7.25)]


# ---------------------------------------------------------------- launching
def test_launch_round_builds_correct_payloads(seeded):
    fake = FakeClient()
    m = build_manager(seeded, fake)
    recs = asyncio.run(m.launch_round(2, ["printco", "quickprint"], {"printco": "another vendor said 430"}))
    assert len(fake.created) == 2 and all(r.call_id for r in recs)
    p = next(c for c in fake.created if c["customer"]["name"] == "PrintCo Tees")
    assert p["assistantId"] == "asst_buyer" and p["phoneNumberId"] == "pn_buyer"
    v = p["assistantOverrides"]["variableValues"]
    assert v["competing_quote"] == "another vendor said 430" and v["round_number"] == "2"
    assert all(isinstance(x, str) for x in v.values())                      # Vapi templating needs strings
    assert p["metadata"]["vendor_id"] == "printco"
    other = next(c for c in fake.created if c["customer"]["name"] != "PrintCo Tees")
    assert other["assistantOverrides"]["variableValues"]["competing_quote"] == "none"


def test_launch_without_bootstrap_marks_failed(settings):
    m = build_manager(settings, FakeClient())
    rec = asyncio.run(m.launch_call("printco"))
    assert rec.status == CallStatus.FAILED and "bootstrap" in rec.error


def test_event_arriving_before_create_call_returns_still_matches(seeded):
    """Race: Vapi's first webhook can beat the POST /call response."""
    m = build_manager(seeded, FakeClient())
    m.on_status_update({"status": "ringing", "call": {"id": "call_early", "metadata": {"vendor_id": "printco", "record_id": "rec_x"}}})
    assert m.get("call_early").record_id == "rec_x"


def test_sync_from_api_and_completion_handler_fires_once(seeded):
    m = build_manager(seeded, FakeClient())
    seen = []
    m.add_completion_handler(lambda rec: seen.append(rec.record_id))
    rec = asyncio.run(m.launch_call("printco"))
    asyncio.run(m.sync_from_api(rec.record_id))
    asyncio.run(m.sync_from_api(rec.record_id))
    assert rec.status == CallStatus.COMPLETED and seen == [rec.record_id]


def test_records_survive_restart(seeded):
    fake = FakeClient()
    m1 = build_manager(seeded, fake)
    rec = asyncio.run(m1.launch_call("stitchhub"))
    m2 = build_manager(seeded, fake)
    assert m2.get(rec.call_id).vendor_id == "stitchhub"