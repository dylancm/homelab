"""Mealie API client.

Phase-1 stub. Same convention as grocy.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx


@dataclass
class MealPlanEntry:
    date: date
    recipe_id: str
    servings_multiplier: float


class MealieClient:
    def __init__(self, base_url: str, api_token: str, household_id: int = 1) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_token}"},
            timeout=httpx.Timeout(10.0),
        )
        self.household_id = household_id

    async def aclose(self) -> None:
        await self._client.aclose()

    async def ping(self) -> bool:
        """Liveness check used by /healthz."""
        r = await self._client.get("/api/app/about")
        return r.status_code == 200

    async def get_mealplan(self, start: date, end: date) -> list[MealPlanEntry]:
        raise NotImplementedError("week 3")

    async def get_recipe(self, recipe_id: str) -> dict[str, Any]:
        raise NotImplementedError("week 3")

    async def push_shopping_list(self, name: str, items: list[dict[str, Any]]) -> str:
        """Create a shopping list in Mealie so the wife sees it in-app."""
        raise NotImplementedError("week 4")
