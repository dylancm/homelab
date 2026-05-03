"""Order export to xlsx/csv/txt. Stub; implemented in week 4."""

from __future__ import annotations

from typing import Iterable, Literal


ExportFormat = Literal["xlsx", "csv", "txt"]
DEFAULT_FIELDS = ("name", "qty", "unit", "sku", "url", "price", "reason")


def export(
    items: Iterable[dict],
    fmt: ExportFormat,
    fields: tuple[str, ...] = DEFAULT_FIELDS,
) -> bytes:
    raise NotImplementedError("week 4")
