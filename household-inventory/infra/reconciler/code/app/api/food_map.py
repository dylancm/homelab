"""POST /food-map/reload — hot-reload food_map.yaml."""

from __future__ import annotations

from fastapi import APIRouter, Request


router = APIRouter()


@router.post("/food-map/reload")
async def reload_food_map(request: Request) -> dict:
    count = request.app.state.food_map.reload()
    return {"loaded": count}
