"""Orders endpoints — week 4 work."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException


router = APIRouter()


@router.get("/orders")
async def list_orders() -> list[dict]:
    raise HTTPException(status_code=501, detail="week 4")


@router.get("/orders/{order_id}")
async def get_order(order_id: int) -> dict:
    raise HTTPException(status_code=501, detail="week 4")


@router.get("/orders/{order_id}/export")
async def export_order(order_id: int, format: str = "xlsx") -> dict:
    raise HTTPException(status_code=501, detail="week 4")


@router.post("/orders/{order_id}/mark-submitted")
async def mark_submitted(order_id: int) -> dict:
    raise HTTPException(status_code=501, detail="week 4")


@router.post("/orders/{order_id}/mark-received")
async def mark_received(order_id: int) -> dict:
    raise HTTPException(status_code=501, detail="week 4")


@router.get("/orders/{order_id}/print")
async def print_order(order_id: int) -> dict:
    raise HTTPException(status_code=501, detail="week 4")
