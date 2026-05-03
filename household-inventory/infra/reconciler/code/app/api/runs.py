"""Reconciliation run history — week 4 work."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException


router = APIRouter()


@router.get("/runs")
async def list_runs() -> list[dict]:
    raise HTTPException(status_code=501, detail="week 4")


@router.get("/runs/{run_id}")
async def get_run(run_id: int) -> dict:
    raise HTTPException(status_code=501, detail="week 4")
