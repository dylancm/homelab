"""Configuration loading. Reads config.yaml + env vars."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class GrocyConfig(BaseModel):
    base_url: str
    api_key: str = ""


class MealieConfig(BaseModel):
    base_url: str
    household_id: int = 1
    api_token: str = ""


class ReconcilerConfig(BaseModel):
    food_map_path: Path = Path("/data/food_map.yaml")
    state_db_path: Path = Path("/data/state.db")
    week_starts_on: Literal["monday", "sunday"] = "monday"
    cron_enabled: bool = True
    cron_day: str = "sunday"
    cron_hour: int = 6


class Secrets(BaseSettings):
    """Secrets loaded from env (never written to disk)."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    grocy_api_key: str = Field(default="", alias="GROCY_API_KEY")
    mealie_api_token: str = Field(default="", alias="MEALIE_API_TOKEN")


class AppConfig(BaseModel):
    grocy: GrocyConfig
    mealie: MealieConfig
    reconciler: ReconcilerConfig


def load_config(path: Path | str = "/app/config.yaml") -> AppConfig:
    """Load config.yaml and merge env-sourced secrets in."""
    p = Path(path)
    if p.exists():
        raw = yaml.safe_load(p.read_text()) or {}
    else:
        # Defaults if no config file is mounted — useful for local dev.
        raw = {
            "grocy": {"base_url": "https://grocy.home.nthparallel.com"},
            "mealie": {"base_url": "https://mealie.home.nthparallel.com"},
            "reconciler": {},
        }
    cfg = AppConfig.model_validate(raw)
    secrets = Secrets()
    cfg.grocy.api_key = secrets.grocy_api_key
    cfg.mealie.api_token = secrets.mealie_api_token
    return cfg
