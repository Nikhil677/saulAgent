"""Data models for call records."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CallStatus(str, Enum):
    QUEUED = "queued"
    RINGING = "ringing"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"      # conversation happened
    NO_ANSWER = "no_answer"      # busy / unanswered / nobody spoke
    VOICEMAIL = "voicemail"
    FAILED = "failed"            # provider/pipeline error or launch failure


TERMINAL_STATUSES = {
    CallStatus.COMPLETED,
    CallStatus.NO_ANSWER,
    CallStatus.VOICEMAIL,
    CallStatus.FAILED,
}


class Turn(BaseModel):
    """One utterance. `start_sec` is the evidence timestamp used by the recommendation layer."""
    role: Literal["agent", "vendor"]
    text: str
    start_sec: float | None = None


class CallRecord(BaseModel):
    record_id: str                       # our id (exists before Vapi assigns a call id)
    call_id: str | None = None           # Vapi call id
    vendor_id: str
    vendor_name: str
    round: int = 1
    status: CallStatus = CallStatus.QUEUED
    created_at: str = Field(default_factory=utc_now_iso)
    started_at: str | None = None
    ended_at: str | None = None
    ended_reason: str | None = None
    duration_sec: float | None = None
    cost: float | None = None
    summary: str | None = None
    transcript: str = ""                 # full text from end-of-call report
    turns: list[Turn] = Field(default_factory=list)       # final, timestamped
    live_turns: list[Turn] = Field(default_factory=list)  # streamed during the call
    recording_url: str | None = None
    competing_quote_used: str | None = None
    error: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES