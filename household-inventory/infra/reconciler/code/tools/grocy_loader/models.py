"""Pydantic models for the loader.

Two flavors:

- `Yaml*` models describe the input file (names, not IDs).
- `Grocy*` models describe Grocy entities the way the API returns/accepts them
  (IDs everywhere). The loader resolves Yaml -> Grocy by walking name maps.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# YAML input
# ---------------------------------------------------------------------------


class YamlConversion(BaseModel):
    """A per-product quantity-unit conversion in the YAML file."""

    model_config = ConfigDict(extra="forbid")

    from_: str = Field(alias="from")
    to: str
    factor: float

    @field_validator("factor")
    @classmethod
    def _factor_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("factor must be > 0")
        return v


class YamlProduct(BaseModel):
    """A single product entry in the YAML file. All names; no IDs."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str | None = None
    barcode: str | None = None
    qu_purchase: str
    qu_stock: str
    conversions: list[YamlConversion] = Field(default_factory=list)
    location: str
    product_group: str | None = None
    shopping_location: str | None = None
    min_stock_amount: float | None = None
    default_best_before_days: int | None = None
    userfields: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = None

    @field_validator("name")
    @classmethod
    def _name_nonempty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be empty")
        return v


class YamlFile(BaseModel):
    """Top-level YAML schema."""

    model_config = ConfigDict(extra="forbid")

    products: list[YamlProduct]


# ---------------------------------------------------------------------------
# Grocy entities (responses)
# ---------------------------------------------------------------------------


class GrocyNamed(BaseModel):
    """A Grocy entity that has at least an id and a name.

    Used for locations, product_groups, shopping_locations, quantity_units.
    """

    model_config = ConfigDict(extra="ignore")

    id: int
    name: str


class GrocyProduct(BaseModel):
    """Subset of fields we care about when looking up an existing product."""

    model_config = ConfigDict(extra="ignore")

    id: int
    name: str


class GrocyConversion(BaseModel):
    """A row in the `quantity_unit_conversions` table for a given product.

    Grocy auto-creates `qu_purchase -> qu_stock` (and its inverse) at product
    creation with factor=1. The loader looks them up to decide whether to
    POST a new record or PUT-update an existing one.
    """

    model_config = ConfigDict(extra="ignore")

    id: int
    product_id: int | None = None
    from_qu_id: int
    to_qu_id: int
    factor: float


class GrocyCreatedObject(BaseModel):
    """Response shape for POST /objects/{entity}.

    Grocy returns `{"created_object_id": "<id>"}`. The id is sometimes an int,
    sometimes a string depending on version, so coerce.
    """

    model_config = ConfigDict(extra="ignore")

    created_object_id: int

    @field_validator("created_object_id", mode="before")
    @classmethod
    def _coerce(cls, v: Any) -> int:
        return int(v)


# ---------------------------------------------------------------------------
# Resolved view: post-name-resolution, ready for write calls
# ---------------------------------------------------------------------------


class ResolvedConversion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_qu_id: int
    to_qu_id: int
    factor: float
    # Original names retained for log output.
    from_name: str
    to_name: str


class ResolvedProduct(BaseModel):
    """A YamlProduct with all names resolved to IDs."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str | None
    barcode: str | None
    qu_id_purchase: int
    qu_id_stock: int
    conversions: list[ResolvedConversion]
    location_id: int
    product_group_id: int | None
    shopping_location_id: int | None
    min_stock_amount: float | None
    default_best_before_days: int | None
    userfields: dict[str, Any]
    # Description gets the YAML `description` plus any `notes` appended.
    full_description: str | None
