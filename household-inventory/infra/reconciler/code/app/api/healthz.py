"""Liveness/readiness — pings Grocy + Mealie."""

from __future__ import annotations

from fastapi import APIRouter, Request


router = APIRouter()


@router.get("/healthz")
async def healthz(request: Request) -> dict:
    grocy_ok = False
    mealie_ok = False
    try:
        grocy_ok = await request.app.state.grocy.ping()
    except Exception:
        pass
    try:
        mealie_ok = await request.app.state.mealie.ping()
    except Exception:
        pass
    return {
        "status": "ok" if (grocy_ok and mealie_ok) else "degraded",
        "grocy": grocy_ok,
        "mealie": mealie_ok,
        "food_map_entries": len(request.app.state.food_map),
    }
