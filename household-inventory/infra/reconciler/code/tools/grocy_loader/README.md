# Grocy product loader

Bulk-import products into Grocy from a YAML file. Idempotent by product name —
re-running after editing the YAML only adds new entries.

This is meant for one-shot vendor list imports (HEB Buy It Again, Amazon
Subscribe & Save, Costco, Home Depot, etc.). Each vendor gets its own YAML
file you maintain by hand.

## Quick start

```bash
cd infra/reconciler/code

# 1. Install deps (typer, python-dotenv, pytest-httpx already in pyproject).
uv sync

# 2. Configure auth.
cp tools/grocy_loader/.env.example tools/grocy_loader/.env
$EDITOR tools/grocy_loader/.env       # set GROCY_API_KEY

# 3. Dry-run first — proves name resolution works, makes ZERO writes.
uv run python -m tools.grocy_loader \
  --dry-run \
  --input tools/grocy_loader/products.sample.yaml

# 4. Real run.
uv run python -m tools.grocy_loader \
  --input tools/grocy_loader/products.sample.yaml
```

## What it does

For each product in the YAML:

1. Looks up the product by name. If it exists, **skip** it.
2. Otherwise: `POST /objects/products` with the resolved IDs.
3. If `barcode` is set: `POST /objects/product_barcodes`.
4. For each entry in `conversions`: `POST /objects/quantity_unit_conversions`
   with `product_id` set.
5. For each entry in `userfields`: `PUT /userfields/products/{id}` (one PUT
   per key). Grocy merges by key, so existing userfields are preserved.

Pre-flight: every name in the YAML (`location`, `product_group`,
`shopping_location`, `qu_purchase`, `qu_stock`, conversion endpoints) is
resolved to an ID before any write call is made. If anything doesn't resolve,
the loader prints all unresolved names grouped by type and exits non-zero
without writing anything. This is on purpose — it's the failure mode you
actually want to prevent.

## Re-run safety

The loader's idempotency unit is the product `name` (case-insensitive, after
trim). If a product with that exact name exists, the loader skips it and
moves on — it does NOT touch barcodes, conversions, or userfields on the
existing product.

This means:

- **Adding** a new product to the YAML and re-running: ✓ creates only the
  new product.
- **Editing** an existing product's YAML entry and re-running: NOOP —
  existing product is left as-is. Edit it manually in Grocy, or extend the
  loader to support updates (out of scope for now).
- **Renaming** a product: creates a duplicate. Avoid.

## CLI

```
python -m tools.grocy_loader [OPTIONS]

  --input, -i PATH     Path to YAML file (default: products.yaml in CWD)
  --dry-run            Resolve and print, no writes
  --verbose, -v        Show INFO logs (HTTP setup, name-map fetches)
```

## Configuration

Required environment variables (loaded from `tools/grocy_loader/.env` if
present, otherwise from CWD `.env`, otherwise from the actual environment):

- `GROCY_BASE_URL` — e.g. `https://grocy.home.nthparallel.com` (no trailing `/api`)
- `GROCY_API_KEY` — the value of the `GROCY-API-KEY` header. **Must have
  `MASTER_DATA_EDIT` permission** — read-only keys will get 403 on every
  POST/PUT. Verify in Grocy under Manage API keys when you create the key.

## Notes from verifying against the live Grocy API

Built and verified against Grocy 4.6.0 (OpenAPI spec + empirical writes).
Things that differ from naive guesses:

- **`qu_factor_purchase_to_stock` is not a Product field anymore.** Grocy 4.6
  moved purchase->stock conversion entirely into the `quantity_unit_conversions`
  table. The loader does NOT set this field on the product.
- **Grocy auto-creates the `qu_purchase -> qu_stock` conversion (and its
  inverse) when a product is created**, with `factor=1` by default. POSTing
  the same `(from, to)` pair again returns `400 "QU conversion already
  exists"`. So the loader does an upsert: after creating each product it
  lists the product's existing conversions, and for each YAML conversion it
  PUT-updates the matching record's factor if one exists, else POSTs a new
  record. PUT-updating one direction also auto-updates the inverse to
  `1/factor`, which is what you want.
- **Userfield keys are validated server-side**: PUT /userfields/products/{id}
  with a key not registered as a userfield definition returns `400 "Field
  <name> is not a valid userfield"`. The loader fetches the userfield key
  set during pre-flight and treats unknown keys exactly like unknown
  location/QU names — abort, no writes, list every offender.
- **Userfield PUT merges by key**, not replaces. Empirically verified: PUT
  `{a: 1}` followed by PUT `{b: 2}` leaves `{a: 1, b: 2}`. The loader sends
  one PUT per key for clean per-key error reporting.
- **`created_object_id` is returned as a string**, not an integer (`"42"`).
  The loader's Pydantic model coerces with `mode="before"`.
- **`qu_id` on a product barcode** is intentionally omitted. If set, Grocy
  interprets it as "this barcode means N of `qu_id` units"; we just want a
  plain barcode lookup, so we leave it null.

## Pre-existing master data

The loader treats this as pre-existing — it never creates locations, product
groups, shopping locations, quantity units, or userfield definitions. If a
YAML name doesn't resolve, pre-flight aborts with the full list. Add the
missing entities in Grocy's UI first, then re-run.

For tablespoon/teaspoon and other recipe units, add the QU in Grocy as a
global unit, then use it in per-product conversions in your YAML.
Cup-to-gram factors are product-specific (flour ≠ sugar ≠ rice), so set
those per-product, not globally.

For arbitrary metadata fields (`heb_sku`, `pack_size_grams`, etc.), define
them as userfields under Manage master data → Userfields → Entity:
`products` before referencing them in YAML.

## Output format

```
✓ created: "All-Purpose Flour" (id=42)
→ skip: "Ozarka Spring Water 24-pack" (already exists, id=17)
✗ failed: "Bad Product" — POST /api/objects/products -> 400: ...
    ✗ barcode failed: POST /api/objects/product_barcodes -> 400: ...
    ✗ conversion Bag->g failed: ...
    ✗ userfield 'heb_sku' failed: ...

3 created, 1 skipped, 1 failed.
```

Sub-step failures (barcode/conversion/userfield) do NOT roll back the parent
product — Grocy doesn't support cross-endpoint transactions, and partial
state is recoverable manually. The full error context is logged so you can
fix and re-run; idempotency means re-running won't double-create the
product.

## Testing

```bash
cd infra/reconciler/code
uv run --frozen pytest tools/grocy_loader/test_dry_run.py -v
```

The test exercises both the dry-run path (asserts zero write calls) and a
real run for one product (asserts the exact sequence of POST/PUT calls)
using `pytest-httpx` to mock Grocy.
