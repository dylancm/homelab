"""Grocy API client.

Phase-1 stub: methods are declared at the shape needed by reconcile.py
but raise NotImplementedError. Filled in during week 3-4.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class Product:
    id: int
    name: str
    qu_id_purchase: int
    shopping_location_id: int | None
    userfields: dict[str, Any]


class GrocyClient:
    def __init__(self, base_url: str, api_key: str) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"GROCY-API-KEY": api_key},
            timeout=httpx.Timeout(10.0),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def ping(self) -> bool:
        """Liveness check used by /healthz."""
        r = await self._client.get("/api/system/info")
        return r.status_code == 200

    async def get_product(self, product_id: int) -> Product:
        raise NotImplementedError("week 3")

    async def get_stock(self, product_id: int) -> float:
        raise NotImplementedError("week 3")

    async def get_volatile(self) -> dict[str, Any]:
        """GET /api/stock/volatile — returns missing_products, expiring, etc."""
        raise NotImplementedError("week 3")

    async def convert_qu(self, product_id: int, qty: float, from_unit: str) -> float:
        """Convert qty/unit into the product's purchase QU."""
        raise NotImplementedError("week 3")

    async def last_price(self, product_id: int, vendor_id: int) -> float | None:
        raise NotImplementedError("week 4")

    async def list_shopping_locations(self) -> list[dict[str, Any]]:
        raise NotImplementedError("week 3")
