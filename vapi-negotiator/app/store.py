"""Tiny JSON file store with atomic writes. Good enough for a hackathon."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any


class JsonStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def load(self) -> dict[str, Any]:
        with self._lock:
            if not self.path.exists():
                return {}
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return {}

    def save(self, data: dict[str, Any]) -> None:
        with self._lock:
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)  # atomic on Windows and POSIX