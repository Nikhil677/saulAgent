"""Central configuration. Everything comes from environment variables / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass(frozen=True)
class Settings:
    vapi_api_key: str = ""
    vapi_base_url: str = "https://api.vapi.ai"
    public_base_url: str = ""          # e.g. https://abc.trycloudflare.com (no trailing slash)
    webhook_secret: str = ""

    llm_provider: str = "anthropic"
    llm_model: str = ""                # must be set before creating assistants

    buyer_voice_provider: str = "vapi"
    buyer_voice_id: str = "Elliot"
    vendor_voice_provider: str = "vapi"
    vendor_voice_id: str = "Rohan"

    transcriber_provider: str = "deepgram"
    transcriber_model: str = "nova-2"
    transcriber_language: str = "en"

    max_call_seconds: int = 240
    silence_timeout_seconds: int = 20
    number_area_code: str = "415"

    state_dir: Path = ROOT / "state"
    config_dir: Path = ROOT / "config"

    @property
    def webhook_url(self) -> str:
        return f"{self.public_base_url.rstrip('/')}/vapi/webhook" if self.public_base_url else ""

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            vapi_api_key=_env("VAPI_API_KEY"),
            vapi_base_url=_env("VAPI_BASE_URL", "https://api.vapi.ai"),
            public_base_url=_env("PUBLIC_BASE_URL"),
            webhook_secret=_env("VAPI_WEBHOOK_SECRET"),
            llm_provider=_env("VAPI_LLM_PROVIDER", "anthropic"),
            llm_model=_env("VAPI_LLM_MODEL"),
            buyer_voice_provider=_env("BUYER_VOICE_PROVIDER", "vapi"),
            buyer_voice_id=_env("BUYER_VOICE_ID", "Elliot"),
            vendor_voice_provider=_env("VENDOR_VOICE_PROVIDER", "vapi"),
            vendor_voice_id=_env("VENDOR_VOICE_ID", "Rohan"),
            transcriber_provider=_env("TRANSCRIBER_PROVIDER", "deepgram"),
            transcriber_model=_env("TRANSCRIBER_MODEL", "nova-2"),
            transcriber_language=_env("TRANSCRIBER_LANGUAGE", "en"),
            max_call_seconds=int(_env("MAX_CALL_SECONDS", "240")),
            silence_timeout_seconds=int(_env("SILENCE_TIMEOUT_SECONDS", "20")),
            number_area_code=_env("NUMBER_AREA_CODE", "415"),
        )