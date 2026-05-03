"""Reconciliation algorithm.

Implements the pseudocode in phase-1-setup.md §4.5. Phase-1 ships the
control flow with the IO calls stubbed; week 3-4 fills in the client
methods this depends on.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from .food_map import FoodMap
from .grocy import GrocyClient
from .mealie import MealieClient


@dataclass
class ReconciliationResult:
    week_start: date
    unmapped_foods: list[str] = field(default_factory=list)
    products_below_min: int = 0
    products_added_for_meals: int = 0
    by_vendor: dict[int, list[dict]] = field(default_factory=dict)


async def reconcile_week(
    week_start: date,
    grocy: GrocyClient,
    mealie: MealieClient,
    food_map: FoodMap,
) -> ReconciliationResult:
    """See PRD §4.5. Returns aggregated state; persistence is in a separate
    pass so this function stays pure-ish and testable."""
    week_end = week_start + timedelta(days=6)
    result = ReconciliationResult(week_start=week_start)

    # 1-2. Aggregate ingredients across the week's meal plan.
    needed: dict[tuple[str, str], float] = defaultdict(float)
    plan = await mealie.get_mealplan(week_start, week_end)
    for entry in plan:
        recipe = await mealie.get_recipe(entry.recipe_id)
        for ing in recipe.get("recipe_ingredient", []):
            food = (ing.get("food") or {}).get("name")
            if not food:
                continue
            unit = ((ing.get("unit") or {}).get("name") or "").lower()
            qty = float(ing.get("quantity") or 0)
            needed[(food.lower(), unit)] += qty * entry.servings_multiplier

    # 3. Map Mealie foods -> Grocy products.
    grocy_needed: dict[int, float] = {}
    for (food_name, unit), qty in needed.items():
        product_id = food_map.get(food_name)
        if product_id is None:
            result.unmapped_foods.append(food_name)
            continue
        converted = await grocy.convert_qu(product_id, qty, unit)
        grocy_needed[product_id] = grocy_needed.get(product_id, 0.0) + converted

    # 4. Subtract current stock to get net meal-plan shortage.
    meal_shortage: dict[int, float] = {}
    for product_id, needed_qty in grocy_needed.items():
        in_stock = await grocy.get_stock(product_id)
        shortage = max(0.0, needed_qty - in_stock)
        if shortage > 0:
            meal_shortage[product_id] = shortage

    # 5. Pull below-min-stock independently.
    volatile = await grocy.get_volatile()
    below_min = {
        int(p["product_id"]): float(p["amount_missing"])
        for p in volatile.get("missing_products", [])
    }

    # 6. Merge: max() not sum() — staples and meal-plan can both demand.
    to_buy: dict[int, dict] = {}
    for pid, qty in meal_shortage.items():
        to_buy[pid] = {"qty": qty, "reason": "meal_plan_shortage"}
    for pid, qty in below_min.items():
        if pid in to_buy:
            to_buy[pid]["qty"] = max(to_buy[pid]["qty"], qty)
            to_buy[pid]["reason"] = "both"
        else:
            to_buy[pid] = {"qty": qty, "reason": "below_min_stock"}

    result.products_below_min = len(below_min)
    result.products_added_for_meals = len(meal_shortage)

    # 7. Group by preferred vendor.
    by_vendor: dict[int, list[dict]] = defaultdict(list)
    for pid, info in to_buy.items():
        product = await grocy.get_product(pid)
        vendor_id = product.shopping_location_id
        if vendor_id is None:
            # No preferred vendor set — bucket under 0 ("unassigned").
            vendor_id = 0
        by_vendor[vendor_id].append(
            {
                "product_id": pid,
                "name": product.name,
                "qty": info["qty"],
                "reason": info["reason"],
            }
        )
    result.by_vendor = dict(by_vendor)
    return result
