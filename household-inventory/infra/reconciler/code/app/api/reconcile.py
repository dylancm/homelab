"""POST /reconcile/week — runs the algorithm for a given week."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Request


router = APIRouter()


@router.post("/reconcile/week")
async def reconcile_week_endpoint(
    request: Request, start: date, force: bool = False
) -> dict:
    # Phase 1 stub: control flow is implemented in app.reconcile but its
    # client deps are NotImplementedError until week 3-4. Wiring this up
    # without a live Grocy/Mealie behind it would 500 confusingly, so we
    # explicitly 501 until the clients are filled in.
    raise HTTPException(
        status_code=501,
        detail=(
            "reconcile/week is scaffolded but the Grocy/Mealie client "
            "methods it depends on are not yet implemented. See "
            "phase-1-setup.md §4.5 (week 3-4 work)."
        ),
    )
