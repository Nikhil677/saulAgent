"""Configuration management for Vapi Negotiator.

Loads application settings and credentials from environment variables and .env file.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Base directory for the vapi-negotiator project
BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Application settings and environment configuration."""
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False
    )

    # Vapi API Credentials
    vapi_api_key: str = Field(default="", description="Vapi Private API Key")
    vapi_public_key: str = Field(default="", description="Vapi Public Key for web calls")
    vapi_base_url: str = Field(default="https://api.vapi.ai", description="Vapi REST endpoint")

    # Webhook Settings
    webhook_url: str = Field(
        default="http://localhost:8000/vapi/webhook",
        description="Publicly reachable webhook endpoint for Vapi events"
    )
    webhook_secret: Optional[str] = Field(
        default=None,
        description="Optional secret token for authenticating webhook requests"
    )

    # Server Settings
    server_host: str = Field(default="0.0.0.0", description="FastAPI bind host")
    server_port: int = Field(default=8000, description="FastAPI port")

    # File Paths
    data_dir: Path = Field(
        default=BASE_DIR / "data",
        description="Directory for local state and store persistence"
    )
    store_file_path: Path = Field(
        default=BASE_DIR / "data" / "store.json",
        description="File path for JSON data store"
    )
    brief_file_path: Path = Field(
        default=BASE_DIR / "config" / "brief.json",
        description="File path for buyer brief specification"
    )
    vendors_file_path: Path = Field(
        default=BASE_DIR / "config" / "vendors.json",
        description="File path for vendor personas definition"
    )

    # Assistant & Model Defaults
    default_model_provider: str = Field(default="openai")
    default_model: str = Field(default="gpt-4o")
    default_temperature: float = Field(default=0.7)

    # Phone Number ID (Optional, for outbound telephony)
    vapi_phone_number_id: Optional[str] = Field(
        default=None,
        description="Optional default outbound Vapi/Twilio phone number ID"
    )

    def ensure_directories(self) -> None:
        """Create required runtime directories if they do not exist."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.store_file_path.parent.mkdir(parents=True, exist_ok=True)
        self.brief_file_path.parent.mkdir(parents=True, exist_ok=True)
        self.vendors_file_path.parent.mkdir(parents=True, exist_ok=True)


# Global settings singleton
settings = Settings()
settings.ensure_directories()
