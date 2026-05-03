"""Mealie food name -> Grocy product ID mapping. Hot-reloadable."""

from __future__ import annotations

from pathlib import Path
from threading import Lock

import yaml


class FoodMap:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = Lock()
        self._mappings: dict[str, int] = {}
        self.reload()

    def reload(self) -> int:
        """Re-read the YAML file. Returns the count of mappings loaded."""
        if not self._path.exists():
            with self._lock:
                self._mappings = {}
            return 0
        raw = yaml.safe_load(self._path.read_text()) or {}
        mappings = raw.get("mappings", {}) or {}
        with self._lock:
            self._mappings = {k.lower(): int(v) for k, v in mappings.items()}
        return len(self._mappings)

    def get(self, food_name: str) -> int | None:
        with self._lock:
            return self._mappings.get(food_name.lower())

    def __len__(self) -> int:
        with self._lock:
            return len(self._mappings)
