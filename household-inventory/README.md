# household-inventory

Phase 1 of the household inventory system: Grocy (stock) + Mealie (meal planning) + a Python reconciler that turns "this week's meal plan" into a vendor-split shopping list.

## Layout

```
household-inventory/
├── phase-1-setup.md      # The spec — read this first.
├── MANUAL-STEPS.md       # Checklist of stuff to run by hand (privileged, web UIs).
├── infra/
│   ├── caddy/kitchen.caddy             # Snippet to append to /etc/caddy/Caddyfile.
│   ├── grocy/docker-compose.yml        # LSIO Grocy stack.
│   ├── mealie/docker-compose.yml       # Mealie + Postgres.
│   └── reconciler/                     # FastAPI reconciler (Phase-1 stub; algorithm is week 3-4).
│       ├── docker-compose.yml
│       ├── Dockerfile
│       ├── data/food_map.yaml          # Mealie-food -> Grocy-product map (you maintain this).
│       └── code/                       # uv-managed Python project.
└── scripts/
    └── kitchen-bootstrap.sh            # Run on kitchen-vm; installs Docker, syncs stacks, brings up Grocy + Mealie.
```

## Where you are in the build

| Done | Item |
|---|---|
| ✓ | All compose files staged |
| ✓ | Reconciler scaffold (FastAPI app, /healthz, /docs, SQLite schema, lifespan, scheduler init); 3/3 tests pass; ruff + pyright clean |
| ✓ | VM bootstrap script |
| ✓ | Caddy snippet |
| → | **Run `MANUAL-STEPS.md`** to provision, deploy, configure |
| pending | Fill in Grocy/Mealie client methods (week 3-4) |
| pending | Wire `POST /reconcile/week` to the algorithm + order persistence + exports (week 4) |

## Reconciler local dev

```bash
cd infra/reconciler/code
uv sync
uv run --frozen pytest -q
uv run --frozen ruff format .
uv run --frozen ruff check .
uv run --frozen pyright app
uv run uvicorn app.main:app --reload --port 9000   # http://localhost:9000/docs
```
