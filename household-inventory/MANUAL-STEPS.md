# Manual Steps

What's already done, and what's left for you to do by hand.

---

## Already done by the agent

| Step | Status |
|---|---|
| `vm-create --name kitchen-vm --ram 8192 --cpus 4 --disk 40G` | ✓ VM at `10.0.1.110`, MAC `52:54:00:23:6e:14` |
| Resize zvol to 40G + online grow (vm-create's `--disk` flag is documented but not honored — see follow-up below) | ✓ filesystem now 38G usable |
| ZFS snapshots: `@fresh`, `@before-stack`, `@stack-up` | ✓ |
| Rsync repo to `~/household-inventory/` on kitchen-vm | ✓ |
| Bootstrap (Docker install, /srv/kitchen tree, Mealie DB password, compose up) | ✓ |
| Grocy + Mealie + Postgres containers running and healthy | ✓ |
| Append `infra/caddy/kitchen.caddy` to `/etc/caddy/Caddyfile` and reload | ✓ |
| Caddy front-ends verified (grocy 302, mealie 200, kitchen-api 502 as expected) | ✓ |

---

## What you still need to do

### 1. OPNsense DNS host overrides

Without these, the public hostnames don't resolve from your LAN.

Services → Unbound DNS → Overrides → add three host overrides, all → `10.0.1.25` (sim-server / Caddy):

| Host | Domain | IP |
|---|---|---|
| `grocy` | `home.nthparallel.com` | `10.0.1.25` |
| `mealie` | `home.nthparallel.com` | `10.0.1.25` |
| `kitchen-api` | `home.nthparallel.com` | `10.0.1.25` |

Apply. Verify from a LAN host:

```bash
dig +short grocy.home.nthparallel.com   # → 10.0.1.25
```

(Static DHCP lease for kitchen-vm is *optional* — Caddy upstreams use the auto-registered `kitchen-vm.home.nthparallel.com` DNS name, so DHCP IP changes won't break anything.)

### 2. Grocy initial config (web UI)

https://grocy.home.nthparallel.com — walk phase-1-setup.md §2.1 steps 1-9:

- admin/admin → change pwd
- create `reconciler` user (Stock journal + Shopping list roles)
- create wife's user (same roles)
- disable feature flags you don't need
- create Locations and Stores
- add Userfields on `products` and `shopping_locations`
- **create an API key tied to the `reconciler` user — save it**

Snapshot when done:
```bash
sudo zfs snapshot tank/vms/kitchen-vm@grocy-configured
```

### 3. Mealie initial config (web UI)

https://mealie.home.nthparallel.com — walk phase-1-setup.md §2.2 steps 1-9:

- first-time wizard → admin user
- add wife as User
- enable URL Scraping
- disable Allow Signup
- **Profile → Manage API Tokens → create `reconciler` token — save it**
- Data Management → Seed Foods, Units, Categories
- verify cup ↔ ml conversions

Snapshot when done:
```bash
sudo zfs snapshot tank/vms/kitchen-vm@mealie-configured
```

### 4. Start the reconciler

SSH in, fill in `.env`, bring it up:

```bash
ssh dylan@kitchen-vm.home.nthparallel.com
vim /srv/kitchen/reconciler/.env
# GROCY_API_KEY=<from step 2>
# MEALIE_API_TOKEN=<from step 3>

cd /srv/kitchen/reconciler
docker compose up -d
docker compose logs -f reconciler   # ctrl-c when "reconciler started" appears
```

Verify:
```bash
curl -s https://kitchen-api.home.nthparallel.com/healthz
# Expected: {"status":"degraded","grocy":true,"mealie":true,"food_map_entries":0}
# "degraded" until food_map.yaml is populated; both upstreams should be true.
```

OpenAPI: https://kitchen-api.home.nthparallel.com/docs

`POST /reconcile/week` returns **501 Not Implemented** by design — the algorithm scaffold is there but the Grocy/Mealie client methods are stubs (week 3-4 work per phase-1-setup.md).

Snapshot when done:
```bash
sudo zfs snapshot tank/vms/kitchen-vm@reconciler-v0.1
```

### 5. Initial data population (phase-1-setup.md §3)

- Grocy: seed your top ~50 products (CSV import: Settings → Manage master data → Products → Import).
- Grocy: physical stock-take.
- Mealie: import 20 recipes you actually cook.
- Edit `/srv/kitchen/reconciler/data/food_map.yaml` to map every Mealie food name to its Grocy product ID. Hot-reload:
  ```bash
  curl -X POST https://kitchen-api.home.nthparallel.com/food-map/reload
  ```

### 6. Fill in the reconciler algorithm (weeks 3-4)

The scaffold matches phase-1-setup.md §4.5. To make `POST /reconcile/week` work:

| File | What to implement |
|---|---|
| `infra/reconciler/code/app/grocy.py` | `get_product`, `get_stock`, `get_volatile`, `convert_qu`, `last_price`, `list_shopping_locations` |
| `infra/reconciler/code/app/mealie.py` | `get_mealplan`, `get_recipe`, `push_shopping_list` |
| `infra/reconciler/code/app/api/reconcile.py` | replace 501 with `await reconcile_week(...)` from `app/reconcile.py` |
| Order persistence | new module; SQLite schema in `app/schema.sql` already exists |
| `infra/reconciler/code/app/exports.py` | xlsx/csv/txt export |
| `app/main.py::_weekly_tick` | call reconcile + persist |

Local dev:
```bash
cd infra/reconciler/code
uv run --frozen pytest -q
uv run --frozen ruff format .
uv run --frozen ruff check .
uv run --frozen pyright app
```

---

## Follow-ups / known issues

- **`vm-create --disk` is silently ignored.** The script logs the requested size but never resizes the cloned zvol — it stays at the base image's 20G. Manually fixed for this VM via `sudo zfs set volsize=40G` + `virsh blockresize` + `growpart` + `resize2fs`. Worth fixing in `/usr/local/bin/vm-create` so future VMs honor the flag.
- **Mealie tag `v3.16.1` doesn't exist** on ghcr — phase-1-setup.md and the compose file have been corrected to `v3.16.0` (the actual latest).
- **Bootstrap now uses hex (not base64) for the Mealie DB password** so it can't contain `/`/`+`/`=` (which broke the SQLAlchemy URL on first attempt).

## What's deferred to Phase 2

phase-1-setup.md §7 — MCP wrappers, Apprise, OFF cache, vendor automation, receipt OCR, etc.
