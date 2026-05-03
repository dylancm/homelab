"""FastAPI entrypoint. See phase-1-setup.md §4."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI

from . import db
from .api import food_map as food_map_api
from .api import healthz, orders, reconcile, runs
from .config import AppConfig, load_config
from .food_map import FoodMap
from .grocy import GrocyClient
from .mealie import MealieClient


logging.basicConfig(
    level=logging.INFO,
    format='{"ts":"%(asctime)s","lvl":"%(levelname)s","msg":%(message)r}',
)
log = logging.getLogger("reconciler")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    cfg: AppConfig = load_config(os.environ.get("CONFIG_PATH", "/app/config.yaml"))
    app.state.config = cfg
    app.state.db = db.connect(cfg.reconciler.state_db_path)
    app.state.food_map = FoodMap(cfg.reconciler.food_map_path)
    app.state.grocy = GrocyClient(cfg.grocy.base_url, cfg.grocy.api_key)
    app.state.mealie = MealieClient(
        cfg.mealie.base_url, cfg.mealie.api_token, cfg.mealie.household_id
    )

    scheduler = AsyncIOScheduler(timezone=os.environ.get("TZ", "UTC"))
    if cfg.reconciler.cron_enabled:
        scheduler.add_job(
            _weekly_tick,
            "cron",
            day_of_week=cfg.reconciler.cron_day[:3],
            hour=cfg.reconciler.cron_hour,
            minute=0,
            args=[app],
        )
    scheduler.start()
    app.state.scheduler = scheduler

    log.info("reconciler started: food_map=%d entries", len(app.state.food_map))
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        await app.state.grocy.aclose()
        await app.state.mealie.aclose()
        app.state.db.close()


async def _weekly_tick(app: FastAPI) -> None:
    log.info("weekly cron tick fired (Phase 1 stub — wired in week 4)")


app = FastAPI(
    title="Household Reconciler",
    version="0.1.0",
    description=(
        "Joins Mealie meal plans + Grocy stock into vendor-split shopping "
        "lists. See phase-1-setup.md §4 for the spec."
    ),
    lifespan=lifespan,
)

app.include_router(healthz.router)
app.include_router(reconcile.router)
app.include_router(orders.router)
app.include_router(runs.router)
app.include_router(food_map_api.router)
