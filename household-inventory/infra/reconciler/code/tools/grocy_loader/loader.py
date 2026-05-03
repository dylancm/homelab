"""Load orchestration: parse YAML, resolve names, create products."""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

import yaml

from .grocy_client import GrocyAPIError, GrocyClient
from .models import (
    GrocyNamed,
    ResolvedConversion,
    ResolvedProduct,
    YamlFile,
    YamlProduct,
)

log = logging.getLogger("grocy_loader")


# ---------------------------------------------------------------------------
# Name maps
# ---------------------------------------------------------------------------


@dataclass
class NameMaps:
    """Case-insensitive name->id maps fetched from Grocy upfront, plus the
    set of valid userfield keys for the products entity (case-sensitive,
    since Grocy stores those verbatim).
    """

    locations: dict[str, int]
    product_groups: dict[str, int]
    shopping_locations: dict[str, int]
    quantity_units: dict[str, int]
    product_userfield_keys: set[str]

    @classmethod
    def fetch(cls, client: GrocyClient) -> NameMaps:
        return cls(
            locations=_to_map(client.list_named("locations")),
            product_groups=_to_map(client.list_named("product_groups")),
            shopping_locations=_to_map(client.list_named("shopping_locations")),
            quantity_units=_to_map(client.list_named("quantity_units")),
            product_userfield_keys=client.list_userfield_keys("products"),
        )


def _to_map(items: list[GrocyNamed]) -> dict[str, int]:
    return {item.name.strip().casefold(): item.id for item in items}


def _lookup(m: dict[str, int], name: str) -> int | None:
    return m.get(name.strip().casefold())


# ---------------------------------------------------------------------------
# Resolution (pre-flight)
# ---------------------------------------------------------------------------


@dataclass
class Unresolved:
    """Names that couldn't be resolved, grouped by entity type."""

    locations: set[str] = field(default_factory=set)
    product_groups: set[str] = field(default_factory=set)
    shopping_locations: set[str] = field(default_factory=set)
    quantity_units: set[str] = field(default_factory=set)
    product_userfield_keys: set[str] = field(default_factory=set)

    def any(self) -> bool:
        return bool(
            self.locations
            or self.product_groups
            or self.shopping_locations
            or self.quantity_units
            or self.product_userfield_keys
        )

    def report(self, out: TextIO) -> None:
        groups = [
            ("locations", self.locations),
            ("product_groups", self.product_groups),
            ("shopping_locations", self.shopping_locations),
            ("quantity_units", self.quantity_units),
            ("product_userfield_keys", self.product_userfield_keys),
        ]
        for label, names in groups:
            if not names:
                continue
            print(f"\nUnresolved {label}:", file=out)
            for n in sorted(names):
                print(f"  - {n!r}", file=out)


def resolve_all(
    products: list[YamlProduct], maps: NameMaps
) -> tuple[list[ResolvedProduct], Unresolved]:
    """Walk every product, resolve every name. Return resolved list + missing.

    All-or-nothing: if `Unresolved.any()` is True the caller must abort.
    """
    unresolved = Unresolved()
    resolved: list[ResolvedProduct] = []

    for p in products:
        loc_id = _lookup(maps.locations, p.location)
        if loc_id is None:
            unresolved.locations.add(p.location)

        pg_id: int | None = None
        if p.product_group is not None:
            pg_id = _lookup(maps.product_groups, p.product_group)
            if pg_id is None:
                unresolved.product_groups.add(p.product_group)

        sl_id: int | None = None
        if p.shopping_location is not None:
            sl_id = _lookup(maps.shopping_locations, p.shopping_location)
            if sl_id is None:
                unresolved.shopping_locations.add(p.shopping_location)

        qup_id = _lookup(maps.quantity_units, p.qu_purchase)
        if qup_id is None:
            unresolved.quantity_units.add(p.qu_purchase)
        qus_id = _lookup(maps.quantity_units, p.qu_stock)
        if qus_id is None:
            unresolved.quantity_units.add(p.qu_stock)

        resolved_convs: list[ResolvedConversion] = []
        for c in p.conversions:
            from_id = _lookup(maps.quantity_units, c.from_)
            to_id = _lookup(maps.quantity_units, c.to)
            if from_id is None:
                unresolved.quantity_units.add(c.from_)
            if to_id is None:
                unresolved.quantity_units.add(c.to)
            if from_id is not None and to_id is not None:
                resolved_convs.append(
                    ResolvedConversion(
                        from_qu_id=from_id,
                        to_qu_id=to_id,
                        factor=c.factor,
                        from_name=c.from_,
                        to_name=c.to,
                    )
                )

        for key in p.userfields:
            if key not in maps.product_userfield_keys:
                unresolved.product_userfield_keys.add(key)

        # Even if some lookups failed, try to construct a ResolvedProduct
        # using sentinel zeros so the rest of the loop can continue gathering
        # *all* unresolved names. The product won't be returned if anything
        # was unresolved (caller checks `unresolved.any()`).
        full_description = _combine(p.description, p.notes)
        resolved.append(
            ResolvedProduct(
                name=p.name.strip(),
                description=p.description,
                barcode=p.barcode,
                qu_id_purchase=qup_id or 0,
                qu_id_stock=qus_id or 0,
                conversions=resolved_convs,
                location_id=loc_id or 0,
                product_group_id=pg_id,
                shopping_location_id=sl_id,
                min_stock_amount=p.min_stock_amount,
                default_best_before_days=p.default_best_before_days,
                userfields=dict(p.userfields),
                full_description=full_description,
            )
        )

    return resolved, unresolved


def _combine(description: str | None, notes: str | None) -> str | None:
    parts = [s for s in (description, notes) if s and s.strip()]
    if not parts:
        return None
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------


def load_yaml(path: Path) -> YamlFile:
    raw = yaml.safe_load(path.read_text())
    if raw is None:
        raise ValueError(f"{path} is empty or not valid YAML")
    return YamlFile.model_validate(raw)


# ---------------------------------------------------------------------------
# Status output
# ---------------------------------------------------------------------------


@dataclass
class RunSummary:
    created: int = 0
    skipped: int = 0
    failed: int = 0


def _status(symbol: str, msg: str, *, indent: int = 0) -> None:
    print(f"{'    ' * indent}{symbol} {msg}")


# ---------------------------------------------------------------------------
# Per-product write
# ---------------------------------------------------------------------------


def _build_product_body(p: ResolvedProduct) -> dict[str, object]:
    """Build the POST /objects/products body for a resolved product.

    Only fields the loader knows about. Grocy fills defaults for the rest.
    Following the OpenAPI Product schema field names exactly.
    """
    body: dict[str, object] = {
        "name": p.name,
        "location_id": p.location_id,
        "qu_id_purchase": p.qu_id_purchase,
        "qu_id_stock": p.qu_id_stock,
    }
    if p.full_description is not None:
        body["description"] = p.full_description
    if p.product_group_id is not None:
        body["product_group_id"] = p.product_group_id
    if p.shopping_location_id is not None:
        body["shopping_location_id"] = p.shopping_location_id
    if p.min_stock_amount is not None:
        body["min_stock_amount"] = p.min_stock_amount
    if p.default_best_before_days is not None:
        body["default_best_before_days"] = p.default_best_before_days
    return body


def create_product(client: GrocyClient, p: ResolvedProduct) -> RunSummary:
    """Create one product (and barcode/conversions/userfields). Returns a
    RunSummary delta of (1, 0, 0) on success; (0, 0, 1) on top-level failure.

    Sub-step failures don't fail the product itself — they are logged and
    counted as 0 contribution to `failed`. The caller can decide whether to
    treat sub-step errors as overall failures by inspecting the log.
    """
    summary = RunSummary()
    try:
        new_id = client.create_object("products", _build_product_body(p))
    except GrocyAPIError as e:
        _status("✗", f'failed: "{p.name}" — {e}')
        summary.failed += 1
        return summary

    _status("✓", f'created: "{p.name}" (id={new_id})')
    summary.created += 1

    if p.barcode:
        try:
            client.create_object(
                "product_barcodes",
                {"product_id": new_id, "barcode": p.barcode},
            )
        except GrocyAPIError as e:
            _status("✗", f"barcode failed: {e}", indent=1)

    # Conversions: Grocy auto-creates the qu_purchase->qu_stock pair (and its
    # inverse) at product-creation with factor=1. So we upsert: PUT the
    # existing record's factor if Grocy already made one for this (from, to)
    # pair, otherwise POST a new record. Grocy auto-maintains the inverse.
    try:
        existing = {
            (c.from_qu_id, c.to_qu_id): c
            for c in client.list_conversions_for_product(new_id)
        }
    except GrocyAPIError as e:
        _status("✗", f"failed to list existing conversions: {e}", indent=1)
        existing = {}

    for c in p.conversions:
        key = (c.from_qu_id, c.to_qu_id)
        try:
            if key in existing:
                client.update_object(
                    "quantity_unit_conversions",
                    existing[key].id,
                    {"factor": c.factor},
                )
            else:
                client.create_object(
                    "quantity_unit_conversions",
                    {
                        "product_id": new_id,
                        "from_qu_id": c.from_qu_id,
                        "to_qu_id": c.to_qu_id,
                        "factor": c.factor,
                    },
                )
        except GrocyAPIError as e:
            _status(
                "✗",
                f"conversion {c.from_name}->{c.to_name} failed: {e}",
                indent=1,
            )

    for key, value in p.userfields.items():
        try:
            client.put_userfield("products", new_id, {key: value})
        except GrocyAPIError as e:
            _status("✗", f"userfield {key!r} failed: {e}", indent=1)

    return summary


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------


def run(
    *,
    yaml_path: Path,
    base_url: str,
    api_key: str,
    dry_run: bool,
) -> int:
    """Top-level entry point. Returns process exit code."""
    log.info("loading %s", yaml_path)
    parsed = load_yaml(yaml_path)
    log.info("found %d products in YAML", len(parsed.products))

    with GrocyClient(base_url=base_url, api_key=api_key) as client:
        log.info("fetching name maps from %s", base_url)
        maps = NameMaps.fetch(client)
        log.info(
            "name maps: %d locations, %d product_groups, %d shopping_locations, %d quantity_units",
            len(maps.locations),
            len(maps.product_groups),
            len(maps.shopping_locations),
            len(maps.quantity_units),
        )

        resolved, unresolved = resolve_all(parsed.products, maps)
        if unresolved.any():
            print(
                "ERROR: name resolution failed — no writes performed.", file=sys.stderr
            )
            unresolved.report(sys.stderr)
            return 2

        if dry_run:
            for p in resolved:
                _status(
                    "•",
                    (
                        f'would create: "{p.name}" '
                        f"(loc_id={p.location_id}, "
                        f"qu_purchase={p.qu_id_purchase}, qu_stock={p.qu_id_stock}"
                        + (
                            f", pg_id={p.product_group_id}"
                            if p.product_group_id
                            else ""
                        )
                        + (
                            f", sl_id={p.shopping_location_id}"
                            if p.shopping_location_id
                            else ""
                        )
                        + (f", barcode={p.barcode}" if p.barcode else "")
                        + (
                            f", conversions={len(p.conversions)}"
                            if p.conversions
                            else ""
                        )
                        + (f", userfields={len(p.userfields)}" if p.userfields else "")
                        + ")"
                    ),
                )
            print(f"\nDry run: {len(resolved)} would be created or skipped.")
            return 0

        # Real run: idempotent per-product create.
        totals = RunSummary()
        for p in resolved:
            try:
                existing = client.find_product_by_name(p.name)
            except GrocyAPIError as e:
                _status("✗", f'failed: "{p.name}" — lookup error: {e}')
                totals.failed += 1
                continue
            if existing is not None:
                _status(
                    "→",
                    f'skip: "{p.name}" (already exists, id={existing.id})',
                )
                totals.skipped += 1
                continue
            delta = create_product(client, p)
            totals.created += delta.created
            totals.skipped += delta.skipped
            totals.failed += delta.failed

        print(
            f"\n{totals.created} created, {totals.skipped} skipped, {totals.failed} failed."
        )
        return 0 if totals.failed == 0 else 1
