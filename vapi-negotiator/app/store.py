"""Atomic JSON File Store for Vapi Negotiator.

Provides thread-safe and async-safe persistent storage for assistants, phone numbers,
call records, turns, and negotiation rounds using atomic file replacement.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.models import CallRecord, RoundRecord

logger = logging.getLogger(__name__)


class Store:
    """Atomic JSON-backed storage engine."""

    def __init__(self, file_path: Path):
        self.file_path = Path(file_path)
        self._async_lock = asyncio.Lock()
        self._thread_lock = threading.Lock()
        self._data: Dict[str, Any] = self._initial_structure()
        self.load_sync()

    def _initial_structure(self) -> Dict[str, Any]:
        return {
            "buyer_assistant": None,
            "vendor_assistants": {},
            "phone_numbers": {},
            "calls": {},
            "rounds": {},
            "metadata": {
                "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "last_updated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            },
        }

    def load_sync(self) -> Dict[str, Any]:
        """Synchronously load data from disk into memory."""
        with self._thread_lock:
            if not self.file_path.exists():
                self._data = self._initial_structure()
                self._write_atomic_sync(self._data)
                return self._data

            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if not content:
                        self._data = self._initial_structure()
                    else:
                        self._data = json.loads(content)
            except Exception as e:
                logger.error("Failed to read store file %s: %s", self.file_path, e)
                # Fallback to current memory or initialized
                if not self._data:
                    self._data = self._initial_structure()
            return self._data

    def _write_atomic_sync(self, data: Dict[str, Any]) -> None:
        """Write data dictionary to disk atomically using a temp file and os.replace."""
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        data["metadata"]["last_updated"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        serialized = json.dumps(data, indent=2, ensure_ascii=False)

        # Write to temporary file in the same directory to guarantee same filesystem
        temp_dir = self.file_path.parent
        with tempfile.NamedTemporaryFile("w", dir=temp_dir, delete=False, encoding="utf-8") as tf:
            tf.write(serialized)
            tf.flush()
            os.fsync(tf.fileno())
            temp_name = tf.name

        try:
            os.replace(temp_name, self.file_path)
        except Exception:
            if os.path.exists(temp_name):
                os.remove(temp_name)
            raise

    async def save(self) -> None:
        """Asynchronously commit the in-memory data to disk."""
        async with self._async_lock:
            with self._thread_lock:
                self._write_atomic_sync(self._data)

    # -------------------------------------------------------------------------
    # Assistants
    # -------------------------------------------------------------------------
    async def set_buyer_assistant(self, assistant_data: Dict[str, Any]) -> None:
        async with self._async_lock:
            self._data["buyer_assistant"] = assistant_data
            self._write_atomic_sync(self._data)

    async def get_buyer_assistant(self) -> Optional[Dict[str, Any]]:
        async with self._async_lock:
            return self._data.get("buyer_assistant")

    async def set_vendor_assistant(self, vendor_id: str, assistant_data: Dict[str, Any]) -> None:
        async with self._async_lock:
            if "vendor_assistants" not in self._data:
                self._data["vendor_assistants"] = {}
            self._data["vendor_assistants"][vendor_id] = assistant_data
            self._write_atomic_sync(self._data)

    async def get_vendor_assistant(self, vendor_id: str) -> Optional[Dict[str, Any]]:
        async with self._async_lock:
            return self._data.get("vendor_assistants", {}).get(vendor_id)

    async def get_all_assistants(self) -> Dict[str, Any]:
        async with self._async_lock:
            return {
                "buyer": self._data.get("buyer_assistant"),
                "vendors": self._data.get("vendor_assistants", {}),
            }

    # -------------------------------------------------------------------------
    # Phone Numbers
    # -------------------------------------------------------------------------
    async def set_phone_number(self, number_id: str, number_data: Dict[str, Any]) -> None:
        async with self._async_lock:
            if "phone_numbers" not in self._data:
                self._data["phone_numbers"] = {}
            self._data["phone_numbers"][number_id] = number_data
            self._write_atomic_sync(self._data)

    async def get_phone_numbers(self) -> Dict[str, Any]:
        async with self._async_lock:
            return self._data.get("phone_numbers", {})

    # -------------------------------------------------------------------------
    # Call Records
    # -------------------------------------------------------------------------
    async def upsert_call(self, call_record: CallRecord) -> None:
        async with self._async_lock:
            if "calls" not in self._data:
                self._data["calls"] = {}
            self._data["calls"][call_record.call_id] = call_record.model_dump()
            self._write_atomic_sync(self._data)

    async def get_call(self, call_id: str) -> Optional[CallRecord]:
        async with self._async_lock:
            raw = self._data.get("calls", {}).get(call_id)
            if raw:
                return CallRecord.model_validate(raw)
            return None

    async def get_all_calls(self) -> List[CallRecord]:
        async with self._async_lock:
            calls_dict = self._data.get("calls", {})
            return [CallRecord.model_validate(c) for c in calls_dict.values()]

    # -------------------------------------------------------------------------
    # Negotiation Rounds
    # -------------------------------------------------------------------------
    async def upsert_round(self, round_record: RoundRecord) -> None:
        async with self._async_lock:
            if "rounds" not in self._data:
                self._data["rounds"] = {}
            self._data["rounds"][round_record.round_id] = round_record.model_dump()
            self._write_atomic_sync(self._data)

    async def get_round(self, round_id: str) -> Optional[RoundRecord]:
        async with self._async_lock:
            raw = self._data.get("rounds", {}).get(round_id)
            if raw:
                return RoundRecord.model_validate(raw)
            return None

    async def get_all_rounds(self) -> List[RoundRecord]:
        async with self._async_lock:
            rounds_dict = self._data.get("rounds", {})
            return [RoundRecord.model_validate(r) for r in rounds_dict.values()]

    async def clear(self) -> None:
        """Reset the store to default empty state."""
        async with self._async_lock:
            self._data = self._initial_structure()
            self._write_atomic_sync(self._data)
