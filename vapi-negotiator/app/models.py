"""Data models for Vapi Negotiator.

Defines Pydantic schemas for negotiation briefs, vendor personas, call tracking,
conversation turns, status enums, and round summaries.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class CallStatus(str, Enum):
    """Vapi and internal call lifecycle statuses."""
    QUEUED = "queued"
    RINGING = "ringing"
    IN_PROGRESS = "in-progress"
    FORWARDING = "forwarding"
    ENDED = "ended"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class NegotiationOutcome(str, Enum):
    """Classification of the negotiation call result."""
    DEAL_ACCEPTED = "deal_accepted"
    DEAL_REJECTED = "deal_rejected"
    WALK_AWAY = "walk_away"
    COUNTER_OFFER_PENDING = "counter_offer_pending"
    UNKNOWN = "unknown"


class SpeakerRole(str, Enum):
    """Roles in conversation turns."""
    BUYER = "buyer"
    VENDOR = "vendor"
    ASSISTANT = "assistant"
    USER = "user"
    SYSTEM = "system"


class Turn(BaseModel):
    """A single turn or message in a negotiation call."""
    role: str
    message: str
    timestamp: Optional[str] = None
    extracted_price: Optional[float] = None
    sentiment: Optional[str] = None


class CallRecord(BaseModel):
    """Complete record of a negotiation call."""
    call_id: str
    round_id: Optional[str] = None
    vendor_id: str
    vendor_name: str
    assistant_id: str
    phone_number_id: Optional[str] = None
    customer_number: Optional[str] = None
    status: CallStatus = CallStatus.QUEUED
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    cost: Optional[float] = None
    turns: List[Turn] = Field(default_factory=list)
    transcript: Optional[str] = None
    summary: Optional[str] = None
    final_offer_price: Optional[float] = None
    outcome: NegotiationOutcome = NegotiationOutcome.UNKNOWN
    deal_terms: Optional[Dict[str, Any]] = None
    analysis: Optional[Dict[str, Any]] = None
    recording_url: Optional[str] = None
    raw_metadata: Optional[Dict[str, Any]] = None


class Brief(BaseModel):
    """Buyer procurement brief."""
    brief_id: str = "brief-default"
    item: str
    quantity: int
    unit: str = "units"
    currency: str = "USD"
    starting_offer_per_unit: float
    target_price_per_unit: float
    walk_away_price_per_unit: float
    target_total_budget: Optional[float] = None
    maximum_total_budget: Optional[float] = None
    required_delivery_timeline: Optional[str] = None
    desired_payment_terms: Optional[str] = None
    minimum_warranty_years: Optional[int] = None
    negotiation_priorities: List[str] = Field(default_factory=list)
    negotiation_rules: Dict[str, Any] = Field(default_factory=dict)


class VendorVoice(BaseModel):
    """Voice configuration for vendor assistant."""
    provider: str = "11labs"
    voiceId: str = "burt"


class VendorPersona(BaseModel):
    """Simulated vendor persona profile."""
    id: str
    name: str
    contact_name: str
    contact_title: str
    phone_number: Optional[str] = None
    voice: VendorVoice = Field(default_factory=VendorVoice)
    list_price: float
    price_floor: float
    minimum_quantity_for_floor: int = 1
    concession_step: float = 50.0
    negotiation_style: str
    quirks: List[str] = Field(default_factory=list)
    acceptable_payment_terms: List[str] = Field(default_factory=list)
    standard_warranty_years: int = 1
    shipping_policy: Optional[str] = None
    assistant_id: Optional[str] = None
    phone_number_id: Optional[str] = None


class RoundRecord(BaseModel):
    """Record of a multi-vendor negotiation round."""
    round_id: str
    created_at: str
    brief_id: str
    target_price: float
    walk_away_price: float
    call_ids: List[str] = Field(default_factory=list)
    winning_vendor_id: Optional[str] = None
    winning_vendor_name: Optional[str] = None
    best_price: Optional[float] = None
    total_savings: Optional[float] = None
    status: str = "in_progress"  # in_progress, completed, failed
    notes: Optional[str] = None


class LaunchRoundRequest(BaseModel):
    """Request payload to launch a negotiation round."""
    round_name: Optional[str] = None
    vendor_ids: Optional[List[str]] = None
    concurrency: int = 1  # 1 for sequential, >1 for parallel
    simulated: bool = False


class CreateCallRequest(BaseModel):
    """Request payload to launch a single vendor call."""
    vendor_id: str
    phone_number: Optional[str] = None
    simulated: bool = False
