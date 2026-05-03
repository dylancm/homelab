# Household Inventory System — Phase 1 Setup

**Goal of Phase 1:** Get Grocy + Mealie running on a dedicated VM, populate them with a starter dataset, and ship a working reconciler that turns "this week's meal plan" into a shopping list grouped by vendor. Wife can plan meals in Mealie, you can scan groceries into Grocy, and Sunday morning a reconciler-generated shopping list appears in Mealie ready for the HEB curbside order.

**Scope deliberately excluded from Phase 1:** AI agent / MCP wrappers, vendor automation (Instacart IDP, Kroger MCP, Playwright for HEB), receipt OCR, vision lookup, the OFF cache service. Those are Phase 2+.

**Success criteria:**
- Wife can drag recipes onto a week in Mealie's PWA on her phone without help.
- You can scan a grocery item's barcode and have its stock increment in Grocy in under 5 seconds.
- One button (or one cron tick) produces a vendor-split shopping list of "what to buy this week" that accounts for current stock, min-stock thresholds, and the week's planned meals.
- The system survives a VM reboot and a power cycle without manual recovery.

---

## 1. Infrastructure resources to provision

### 1.1 The VM

Provision a dedicated VM via your existing `vm-create` workflow. This isolates the household stack from sim-server's other services and gives you a clean ZFS snapshot point.

```bash
vm-create --name kitchen-vm --ram 8192 --cpus 4 --disk 40G
```

**Why these resources:** Grocy is PHP/SQLite and barely uses anything. Mealie with Postgres is the heavier of the two; 8 GB RAM and 4 vCPUs leave headroom for a household-sized dataset (low hundreds of recipes, low thousands of products) plus the Phase-2 services that will eventually share this VM (OFF cache, MCP wrappers, etc.). 40 GB disk covers the Mealie image cache and gives runway for years of growth. Bump later if needed; ZFS makes resizing the zvol painless.

**After `vm-create` completes:**
1. Assign a static DHCP lease in OPNsense for the printed MAC. Suggested: `10.0.1.30`.
2. Verify `kitchen-vm.home.nthparallel.com` resolves via the OPNsense Unbound auto-registration.
3. Take a `@before-stack` ZFS snapshot before installing anything, so you can rollback to a clean Ubuntu if you want to redo the install:
   ```bash
   sudo zfs snapshot tank/vms/kitchen-vm@before-stack
   ```

### 1.2 DNS host overrides

In OPNsense Unbound, add three explicit host overrides (all pointing to `10.0.1.25` so they hit Caddy on sim-server):

| Hostname | Target |
|---|---|
| `grocy.home.nthparallel.com` | `10.0.1.25` |
| `mealie.home.nthparallel.com` | `10.0.1.25` |
| `kitchen-api.home.nthparallel.com` | `10.0.1.25` |

`kitchen-api` is the reconciler. We name it separately from `kitchen-vm` (the VM) because the VM may host more services later and you want the API hostname to be stable independent of host moves.

### 1.3 Caddy reverse proxy entries on sim-server

Append to your existing Caddyfile:

```caddyfile
grocy.home.nthparallel.com {
    import cloudflare_tls
    reverse_proxy kitchen-vm.home.nthparallel.com:9283
}

mealie.home.nthparallel.com {
    import cloudflare_tls
    reverse_proxy kitchen-vm.home.nthparallel.com:9925
}

kitchen-api.home.nthparallel.com {
    import cloudflare_tls
    reverse_proxy kitchen-vm.home.nthparallel.com:9000
}
```

Reload Caddy: `sudo systemctl reload caddy`. Wildcard-equivalent TLS via your Cloudflare DNS challenge cert covers all three. Verify with `curl -I https://grocy.home.nthparallel.com` from a LAN host once Grocy is up — you should get a 200 or a redirect, not a 502.

Upstreams target the auto-registered DHCP hostname (`kitchen-vm.home.nthparallel.com`) rather than a static IP — that way Caddy keeps working through any IP change without an OPNsense reservation. Set a static DHCP lease anyway if you like consistent IPs, but it's not required for this stack to function.

The non-default ports (9283, 9925, 9000) are deliberate — they don't collide with any of the standard ports anything else uses, and they make `ss -tlnp` output on the VM unambiguous.

### 1.4 Storage layout on the VM

Inside `kitchen-vm`, create a directory layout that maps cleanly to ZFS-snapshot-able units:

```
/srv/kitchen/
├── grocy/        # Grocy's data dir (SQLite DB, uploads, plugins)
├── mealie/
│   ├── postgres/ # Postgres data
│   └── data/     # Mealie's data dir (recipe images, backups)
└── reconciler/
    ├── code/     # git checkout
    └── data/     # SQLite for the reconciler's own state
```

```bash
sudo mkdir -p /srv/kitchen/{grocy,mealie/postgres,mealie/data,reconciler/code,reconciler/data}
sudo chown -R dylan:dylan /srv/kitchen
```

You'll back this whole tree up with a single ZFS snapshot. Don't use Docker named volumes — they hide data inside `/var/lib/docker` and complicate snapshot recovery.

---

## 2. Services to install

All three services run as Docker Compose stacks on `kitchen-vm`. Install Docker per the existing VM-SETUP-GUIDE pattern:

```bash
ssh dylan@kitchen-vm.home.nthparallel.com
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker dylan
exit  # re-login for group to take effect
ssh dylan@kitchen-vm.home.nthparallel.com
```

### 2.1 Grocy

`/srv/kitchen/grocy/docker-compose.yml`:

```yaml
services:
  grocy:
    image: lscr.io/linuxserver/grocy:latest
    container_name: grocy
    restart: unless-stopped
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/Chicago
    volumes:
      - ./data:/config
    ports:
      - "9283:80"
```

Bring it up: `cd /srv/kitchen/grocy && docker compose up -d`.

**Initial Grocy configuration steps (one-time, via web UI at `https://grocy.home.nthparallel.com`):**

1. **Default admin login:** `admin` / `admin`. Change immediately under user settings.
2. **Create a second user** for your wife with the role "Stock journal user" + "Shopping list user" — this hides the admin chrome from her view.
3. **Feature flags are configured via env vars, not the UI.** Grocy reads any `GROCY_<SETTING_NAME>` env var (lowercase `true`/`false` become bools) and these override the defaults in `/app/www/config-dist.php`. The compose file at `infra/grocy/docker-compose.yml` already disables `chores`, `tasks`, `batteries`, `equipment`, and `calendar` (you'll use Mealie's calendar), and enables `label_printer`. Stock, shopping list, recipes, recipes-mealplan, and stock price tracking stay on by default. To change them later: edit `docker-compose.yml`, `docker compose up -d` to recreate the container. The full flag list is `docker exec grocy grep FEATURE_FLAG /app/www/config-dist.php`.
4. **Create Locations** matching your physical layout. Suggested starter set: `Pantry`, `Fridge`, `Freezer`, `Garage`, `Bathroom`, `Laundry Room`, `Office Supplies`. Locations are flat in Grocy; use naming like `Pantry - Top Shelf` if you need pseudo-hierarchy.
5. **Create Stores** (Grocy calls these "Shopping locations"): `HEB`, `Amazon`, `Home Depot`, `Costco`, `Target`. These will become your vendors. Add more as needed.
6. **Create Userfields on Product** (Settings → Manage Userfields → Entity: `products`). This is how we model per-vendor data without forking Grocy:
   - `heb_sku` (text)
   - `heb_url` (link)
   - `amazon_asin` (text)
   - `amazon_url` (link)
   - `homedepot_sku` (text)
   - `homedepot_url` (link)
   - `costco_item_number` (text)
   - `pack_size_grams` (number) — useful for unit math when vendors sell in different package sizes
7. **Create Userfields on Shopping locations** (Entity: `shopping_locations`):
   - `min_order_total` (number) — HEB curbside is $35
   - `fulfillment_mode` (preset list: `delivery`, `curbside`, `in_store`, `subscribe_save`)
   - `order_window` (text) — free-form notes like "Sundays only, ordered by 8pm Sat"
8. **Create an API key** under Settings → Manage API keys. Save it; the reconciler needs it. Grocy API keys are long-lived bearer tokens scoped to a user — create one tied to a dedicated `reconciler` user account so you can revoke it independently of human users.
9. **Take a Grocy snapshot point** by zipping `/srv/kitchen/grocy/data/grocy.db` once you've populated initial config. Keep this as your "clean config" baseline.

### 2.2 Mealie

`/srv/kitchen/mealie/docker-compose.yml`:

```yaml
services:
  mealie:
    image: ghcr.io/mealie-recipes/mealie:v3.16.0
    container_name: mealie
    restart: unless-stopped
    ports:
      - "9925:9000"
    environment:
      ALLOW_SIGNUP: "false"
      PUID: 1000
      PGID: 1000
      TZ: America/Chicago
      BASE_URL: https://mealie.home.nthparallel.com
      DB_ENGINE: postgres
      POSTGRES_USER: mealie
      POSTGRES_PASSWORD: ${MEALIE_DB_PASSWORD}
      POSTGRES_SERVER: postgres
      POSTGRES_PORT: 5432
      POSTGRES_DB: mealie
    volumes:
      - ./data:/app/data
    depends_on:
      postgres:
        condition: service_healthy

  postgres:
    image: postgres:16-alpine
    container_name: mealie-postgres
    restart: unless-stopped
    environment:
      POSTGRES_USER: mealie
      POSTGRES_PASSWORD: ${MEALIE_DB_PASSWORD}
      POSTGRES_DB: mealie
    volumes:
      - ./postgres:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U mealie"]
      interval: 10s
      timeout: 5s
      retries: 5
```

Create `/srv/kitchen/mealie/.env` with `MEALIE_DB_PASSWORD=...` (generate with `openssl rand -base64 32`).

**Pin to a specific version** (`v3.16.0` not `latest`). Mealie publishes breaking changes in minor releases; you want explicit upgrades. When you upgrade later, snapshot ZFS first.

Bring it up: `cd /srv/kitchen/mealie && docker compose up -d`.

**Initial Mealie configuration steps (one-time, via web UI at `https://mealie.home.nthparallel.com`):**

In v3.x there is no single "Settings" menu — admin/household/group concerns are split across three top-level sections accessed from the user/avatar menu.

1. **First-time setup wizard** at `/admin/setup` creates the admin user. Use your email; this becomes the OIDC subject if you wire SSO later.
2. **Households** are managed at **Admin → Manage → Households** (`/admin/manage/households`). The default household created at first-run is fine for one family. (Mealie v2.0+ lets you partition user spaces within a group; for a single household this is invisible.)
3. **Add your wife as a user** at **Admin → Manage → Users → Create** (`/admin/manage/users/create`) with the "User" role (not Admin). Assign her to the same household — she gets meal-plan + shopping-list access without admin chrome.
4. **URL scraping** has no global toggle in v3 — it just works. Import recipes via Recipe → "Create" → "Import from URL".
5. **`ALLOW_SIGNUP` is env-only**, not a UI setting. The compose at `infra/mealie/docker-compose.yml` already passes `ALLOW_SIGNUP: "false"` — defense in depth even though we're internal-only. To verify: `docker exec mealie env | grep ALLOW_SIGNUP`.
6. **Generate a long-lived API token** at **User profile → API Tokens** (`/user/profile/api-tokens`). Name it `reconciler`. The reconciler uses this instead of basic auth.
7. **Notifiers** at **Household → Notifiers** (`/household/notifiers`) — leave empty for Phase 1. Phase 2+ wires Apprise here for low-stock alerts.
8. **Seed the food + unit + category databases** at **Group → Data Management → Foods / Units / Categories** (`/group/data/foods`, `/group/data/units`, `/group/data/categories`). Each page has a "Seed" action that loads ~200 common entries — the recipe parser uses these to recognize ingredients on import.
9. **Unit conversions are NOT a Phase-1 concern.** Mealie has no consolidated unit-conversion table; what it has are per-unit `standardQuantity` / `standardUnit` fields editable in the individual unit edit dialog at `/group/data/units`, plus per-unit `aliases`. None of these are seeded with conversions out of the box. **Skip this step** — the reconciler does all unit math at the Grocy boundary via `/api/objects/quantity_unit_conversions_resolved`, so Mealie-side conversion data is unused in our pipeline.

### 2.3 Reconciler service (placeholder)

For Phase 1, just create the directory structure and a stub. The reconciler PRD in section 4 is what you'll actually build.

```bash
mkdir -p /srv/kitchen/reconciler/code
cd /srv/kitchen/reconciler/code
# Phase 1.5: implement per PRD below
```

You'll deploy this as a third Docker Compose stack on `kitchen-vm` later in the phase. Section 4 has the full spec.

---

## 3. Initial data population

You can have everything running and still have a useless system if it's empty. Plan for ~3 hours of data entry across two weeknights.

### 3.1 Grocy: seed your top 50 products

Don't try to be exhaustive on day one. Pick the 50 items you actually buy weekly and load those. Categories of items to prioritize:

- **Staples** (15–20 items): flour, sugar, salt, olive oil, pasta, rice, canned tomatoes, broth, common spices, eggs, butter, milk, cheese.
- **Cleaning / paper** (8–10 items): paper towels, toilet paper, dish soap, dishwasher detergent, laundry detergent, all-purpose cleaner, trash bags, sponges.
- **Toiletries** (5–8 items): toothpaste, shampoo, conditioner, body wash, deodorant.
- **Recurring meal items** (10–15 items): the proteins and produce that show up in your common meals (chicken breast, ground beef, onions, garlic, etc.).
- **Non-grocery recurring** (3–5 items): furnace filter, water filter cartridges, dishwasher rinse aid.

For each product, set:
- Name, default location, default Quantity Unit (e.g., piece, gram, ml).
- Min stock amount (the trigger for "needs reordering").
- Default best-before days (Grocy uses this to auto-set expiry on purchase).
- Preferred shopping location (the vendor).
- The Userfields you defined: `heb_sku`, `heb_url`, etc. — fill in what you know, leave the rest.

**The fastest way to bulk-create:** Grocy supports CSV import via Settings → Manage master data → Products → Import. Build a CSV in Excel, import in one shot. The product ID schema lets you reference products by name in subsequent shopping list / recipe imports.

### 3.2 Grocy: physical stock-take

Walk the pantry/fridge/freezer with your phone open to Grocy's Stock Overview page in "Inventory" mode. Scan or type each item, set the current quantity. This is tedious one time and never again — from here on, scans on purchase/consumption maintain the count automatically.

If you have a USB barcode scanner, plug it into a laptop in the kitchen and rip through it in 30 minutes. Otherwise the phone camera works.

### 3.3 Mealie: import 20 recipes

Pick 20 recipes that represent your actual meal repertoire — not aspirational ones. The bias should be toward weeknight dinners and weekend breakfasts you actually make.

For each recipe, the import flow is:
1. Recipe → "Create" → "Import from URL" → paste the source URL.
2. Mealie scrapes the recipe, parses ingredients via its NLP parser. Verify the parsed ingredients look right (food, qty, unit on each line).
3. Save.

For recipes that don't have a good source URL (family recipes, Mom's lasagna), use the "Create" → manual entry flow.

### 3.4 The Mealie food → Grocy product mapping

This is the glue that makes the reconciler work. Mealie has its own "foods" table (e.g., `flour, all-purpose`); Grocy has its own products. They don't talk. You maintain a YAML file mapping one to the other.

Create `/srv/kitchen/reconciler/data/food_map.yaml`:

```yaml
# Mealie food name (lowercase, exact) -> Grocy product ID
# Update this whenever you import a new recipe with a new ingredient.
mappings:
  "all-purpose flour": 14
  "ground beef": 22
  "yellow onion": 31
  "olive oil": 8
  "garlic": 19
  # ... etc
```

The reconciler reads this on startup. When it encounters a Mealie food without a mapping, it logs a warning and skips that ingredient (rather than guessing). You'll iterate this file as you import recipes.

This mapping is the single most fragile and most important part of the system. The PRD below treats it as a first-class concern.

---

## 4. Reconciler PRD

### 4.1 Purpose

The reconciler is a Python/FastAPI service that combines Mealie's meal plans + Grocy's stock state to produce vendor-split shopping lists. It is the only piece of the system you write yourself in Phase 1, and it owns the workflow your spec describes:

> "Estimate based on current inventory + projected consumption from planned meals to determine what to add to the order."

### 4.2 Non-goals (explicitly out of scope for Phase 1)

- AI agent / MCP server (the FastAPI OpenAPI surface is sufficient for now; MCP wraps it later).
- Vendor ordering automation (Instacart, Kroger, HEB Playwright). Reconciler emits orders; humans place them.
- Receipt OCR / post-trip stock crediting.
- Vision-based product lookup.
- Notifications (Apprise integration). Slot for Phase 2.
- Multi-household support. One household, period.

### 4.3 Architecture

```
                ┌──────────────────────────┐
                │      Reconciler          │
                │   FastAPI + httpx        │
                │   ┌──────────────────┐   │
                │   │ Mealie client    │───┼──> https://mealie.home.nthparallel.com
                │   │ Grocy client     │───┼──> https://grocy.home.nthparallel.com
                │   │ Order builder    │   │
                │   │ Export (xlsx/csv)│   │
                │   └──────────────────┘   │
                │   SQLite (./data/state.db)│
                │   food_map.yaml          │
                └──────────────────────────┘
                        │
                        │ writes shopping list back
                        ▼
                  Mealie shopping list
                  (so wife sees it in
                   the same app she planned in)
```

**Stack:**
- Python 3.12, managed via `uv` (matches your existing tooling).
- FastAPI for the HTTP surface, with auto-generated OpenAPI at `/docs`.
- `httpx` for async calls to Grocy and Mealie.
- `pydantic` v2 for all data modeling.
- `apscheduler` for the weekly cron tick.
- `openpyxl` for xlsx export, stdlib `csv` for csv export.
- SQLite via `sqlite3` stdlib for local state. No need for Postgres on this service.
- Deployed as a Docker Compose stack on `kitchen-vm` listening on `:9000`, fronted by Caddy at `kitchen-api.home.nthparallel.com`.

### 4.4 Data model (reconciler-local state, SQLite)

```sql
-- Vendor configuration. Mirrors Grocy shopping_locations but enriches with
-- fields that don't fit cleanly in Grocy Userfields.
CREATE TABLE vendors (
    id INTEGER PRIMARY KEY,
    grocy_shopping_location_id INTEGER UNIQUE,
    name TEXT NOT NULL,
    fulfillment_mode TEXT,  -- delivery | curbside | in_store | subscribe_save
    min_order_total REAL,
    notes TEXT
);

-- Each generated weekly shopping list = one Order per vendor.
-- An "order" here is a draft document; the human places the actual order in the vendor's app.
CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    vendor_id INTEGER NOT NULL REFERENCES vendors(id),
    week_start DATE NOT NULL,
    status TEXT NOT NULL,  -- draft | submitted | received | cancelled
    total_estimate REAL,
    notes TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    submitted_at DATETIME,
    received_at DATETIME
);

CREATE TABLE order_items (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    grocy_product_id INTEGER NOT NULL,
    product_name TEXT NOT NULL,           -- denormalized for export/print
    quantity REAL NOT NULL,
    unit TEXT NOT NULL,
    vendor_sku TEXT,
    vendor_url TEXT,
    last_known_price REAL,
    estimated_total REAL,
    reason TEXT  -- "below_min_stock" | "meal_plan_shortage" | "manual"
);

-- Audit log of reconciliation runs, for debugging "why did/didn't X end up on the list?"
CREATE TABLE reconciliation_runs (
    id INTEGER PRIMARY KEY,
    week_start DATE NOT NULL,
    started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    finished_at DATETIME,
    meal_plan_recipe_count INTEGER,
    products_below_min INTEGER,
    products_added_for_meals INTEGER,
    unmapped_foods_count INTEGER,
    notes TEXT  -- JSON blob of warnings, unmapped foods, etc.
);
```

### 4.5 The reconciliation algorithm

This is the core logic. Pseudocode:

```python
def reconcile_week(week_start: date) -> list[Order]:
    week_end = week_start + timedelta(days=6)

    # 1. Pull the week's meal plan from Mealie.
    meal_plan = mealie.get_mealplan(start=week_start, end=week_end)

    # 2. Aggregate ingredients across all planned recipes for the week.
    #    Key: (mealie_food_name, normalized_unit). Value: total qty.
    needed: dict[tuple[str, str], float] = defaultdict(float)
    for entry in meal_plan:
        recipe = mealie.get_recipe(entry.recipe_id)
        for ing in recipe.recipe_ingredient:
            food_name = ing.food.name.lower() if ing.food else None
            if not food_name:
                continue  # free-text ingredient, can't map
            qty, unit = normalize_unit(ing.quantity, ing.unit)
            needed[(food_name, unit)] += qty * entry.servings_multiplier

    # 3. Map Mealie foods to Grocy products via food_map.yaml.
    unmapped = []
    grocy_needed: dict[int, tuple[float, str]] = {}  # product_id -> (qty, unit)
    for (food_name, unit), qty in needed.items():
        product_id = food_map.get(food_name)
        if not product_id:
            unmapped.append(food_name)
            continue
        # If unit doesn't match Grocy's product QU, convert via Grocy's
        # /api/objects/quantity_unit_conversions_resolved
        converted_qty = grocy.convert_qu(product_id, qty, unit)
        grocy_needed[product_id] = (converted_qty, grocy.product_qu(product_id))

    # 4. Subtract current stock to get net shortage from meal plan.
    meal_shortage: dict[int, float] = {}
    for product_id, (needed_qty, unit) in grocy_needed.items():
        in_stock = grocy.get_stock(product_id)
        shortage = max(0, needed_qty - in_stock)
        if shortage > 0:
            meal_shortage[product_id] = shortage

    # 5. Pull products below min_stock_amount independently of the meal plan.
    #    These are staples that need restocking regardless of what we're cooking.
    volatile = grocy.get_volatile()  # /api/stock/volatile
    below_min = {p["product_id"]: p["amount_missing"] for p in volatile["missing_products"]}

    # 6. Merge both sources. A product can appear in both; take the max.
    to_buy: dict[int, dict] = {}
    for pid, qty in meal_shortage.items():
        to_buy[pid] = {"qty": qty, "reason": "meal_plan_shortage"}
    for pid, qty in below_min.items():
        if pid in to_buy:
            to_buy[pid]["qty"] = max(to_buy[pid]["qty"], qty)
            to_buy[pid]["reason"] = "both"
        else:
            to_buy[pid] = {"qty": qty, "reason": "below_min_stock"}

    # 7. Group by preferred vendor.
    by_vendor: dict[int, list] = defaultdict(list)
    for pid, info in to_buy.items():
        product = grocy.get_product(pid)
        vendor_id = product.shopping_location_id
        by_vendor[vendor_id].append({
            "product_id": pid,
            "name": product.name,
            "qty": info["qty"],
            "unit": product.qu_purchase.name,
            "vendor_sku": product.userfields.get(f"{vendor_slug(vendor_id)}_sku"),
            "vendor_url": product.userfields.get(f"{vendor_slug(vendor_id)}_url"),
            "last_price": grocy.last_price(pid, vendor_id),
            "reason": info["reason"],
        })

    # 8. Persist as draft Orders in reconciler SQLite.
    orders = []
    for vendor_id, items in by_vendor.items():
        order = create_order(vendor_id, week_start, items)
        orders.append(order)

    # 9. Push merged shopping list to Mealie so wife sees it in her app.
    push_shopping_list_to_mealie(week_start, by_vendor)

    # 10. Log the run.
    log_reconciliation(week_start, meal_plan, to_buy, unmapped)

    return orders
```

**Edge cases the algorithm must handle:**
- **Unmapped Mealie foods:** log a warning, surface in `/runs/{id}` response, *don't* silently drop. The wife should see "couldn't map: 3 ingredients" in the reconciler UI/output and you fix the mapping file.
- **Recipe with `servings_multiplier`:** Mealie meal plans support scaling a recipe (cooking for 2 vs. 4); multiply ingredient quantities accordingly.
- **Unit conversion failures:** if Grocy doesn't have a conversion path from "cups" to the product's purchase unit, log it and add the item to the list with a `unit_mismatch` flag for human review.
- **Double-counting:** if a product is both below min_stock AND needed by the meal plan, take the max — not the sum.
- **Vendor pack rounding:** if the product has a `pack_size_grams` Userfield, round the requested qty up to the nearest pack. Phase 1 can skip this; Phase 2 adds it.

### 4.6 HTTP API surface

```
POST /reconcile/week?start=YYYY-MM-DD
    Run the reconciliation algorithm for the given week.
    Body: optional { "force": bool }  // re-run even if already run for this week
    Returns: { run_id, orders: [{order_id, vendor, item_count, total_estimate}], unmapped: [...] }

GET /orders
    List all orders, optionally filtered.
    Query: ?status=draft&week_start=YYYY-MM-DD
    Returns: paginated list

GET /orders/{order_id}
    Full order detail with items.

GET /orders/{order_id}/export
    Query: ?format=xlsx|csv|txt&fields=name,qty,unit,sku,url,price,reason
    Returns: file download with selected fields. The toggleable-fields requirement
    from your spec is implemented here as a query param.

POST /orders/{order_id}/mark-submitted
    Body: { "submitted_at": ISO8601, "external_order_ref": str }
    Transitions draft -> submitted. Records when you actually placed the order
    in the vendor's app.

POST /orders/{order_id}/mark-received
    Body: { "received_at": ISO8601, "actual_items": [{product_id, qty, price}] }
    Transitions submitted -> received. For each actual_item, calls Grocy's
    /api/stock/products/{id}/add to credit inventory. If actual differs from
    ordered (substitutions, out-of-stocks), record the delta.

GET /orders/{order_id}/print
    Returns an HTML page styled for printing, with the toggleable fields
    rendered as a clean table. Wife can hit this from her phone if she
    wants a paper list at a store that doesn't have curbside.

GET /runs
    List recent reconciliation runs for debugging.

GET /runs/{run_id}
    Full run detail including unmapped foods, warnings, decisions made.

GET /healthz
    Liveness probe. Returns 200 if Grocy and Mealie API are both reachable.

POST /food-map/reload
    Hot-reload food_map.yaml without restarting the service.
```

### 4.7 The weekly cron tick

`apscheduler` job that runs every Sunday at 6 AM:

```python
@scheduler.scheduled_job("cron", day_of_week="sun", hour=6, minute=0)
def weekly_reconcile():
    week_start = next_monday()
    reconcile_week(week_start)
    # Optional: send a notification (Phase 2 wires Apprise here)
```

The wife wakes up Sunday, opens Mealie, sees the week's shopping list pre-populated. She tweaks (drops a recipe, adds a recipe), and either the next manual `/reconcile/week?force=true` regenerates, or she just edits the Mealie list directly and you live with it for that week.

### 4.8 Configuration

`/srv/kitchen/reconciler/code/config.yaml` (or env vars):

```yaml
grocy:
  base_url: https://grocy.home.nthparallel.com
  api_key: ${GROCY_API_KEY}

mealie:
  base_url: https://mealie.home.nthparallel.com
  api_token: ${MEALIE_API_TOKEN}
  household_id: 1

reconciler:
  food_map_path: /data/food_map.yaml
  state_db_path: /data/state.db
  week_starts_on: monday
  cron_enabled: true
  cron_day: sunday
  cron_hour: 6
```

Secrets via `.env` file. The reconciler should fail fast on startup if either Grocy or Mealie API is unreachable.

### 4.9 Deployment

`/srv/kitchen/reconciler/docker-compose.yml`:

```yaml
services:
  reconciler:
    build: ./code
    container_name: reconciler
    restart: unless-stopped
    ports:
      - "9000:9000"
    environment:
      - GROCY_API_KEY=${GROCY_API_KEY}
      - MEALIE_API_TOKEN=${MEALIE_API_TOKEN}
      - TZ=America/Chicago
    volumes:
      - ./data:/data
    depends_on: []
```

`Dockerfile` in `./code`:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install uv && uv sync --frozen --no-dev
COPY . .
ENV PATH="/app/.venv/bin:$PATH"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9000"]
```

### 4.10 Testing strategy

- **Unit tests** on the reconciliation algorithm with mocked Grocy/Mealie clients. The algorithm is the high-value test surface; the API endpoints are mostly thin wrappers.
- **Integration test** that hits a Grocy + Mealie pair running in Docker (use the same compose files, on a different port) seeded with fixture data. One scenario per: "all in stock," "below min stock," "meal plan shortage," "both," "unmapped food."
- **Snapshot test** on the xlsx/csv/txt export outputs.
- **No tests on the cron**; it's apscheduler, trust it.

### 4.11 Observability

- Structured JSON logs to stdout (Docker captures them).
- Each reconciliation run gets logged with: count of recipes, count of items added by reason, count of unmapped, duration.
- `/healthz` returns Grocy + Mealie reachability.
- For Phase 1, no Prometheus / Grafana. Add later if you want.

---

## 5. Phase 1 timeline

Realistic part-time pacing for someone with a day job:

| Week | Work |
|---|---|
| 1 | VM provisioning, DNS, Caddy, Grocy install + initial config + 50 products, ZFS snapshot at clean state. |
| 2 | Mealie install + initial config, household + users, import 20 recipes, build the Mealie food → Grocy product mapping for those recipes. |
| 3 | Reconciler skeleton: FastAPI scaffolding, Grocy + Mealie clients, /healthz, /reconcile/week happy path (no exports yet). End-to-end test with one recipe and one in-stock-low product. |
| 4 | Reconciler: order persistence in SQLite, /orders endpoints, xlsx/csv/txt exports, print view, mark-submitted/received endpoints. Cron tick. |

Wife uses the system live starting end of week 4. Iterate from real usage; expect to spend weeks 5–6 fixing food mappings, tuning min-stock levels, and adjusting the algorithm based on what's actually showing up on the shopping list.

---

## 6. Snapshots & rollback discipline

Take a ZFS snapshot of `kitchen-vm` at each of these milestones:

- `tank/vms/kitchen-vm@before-stack` — clean Ubuntu, before any service install.
- `tank/vms/kitchen-vm@grocy-configured` — Grocy installed, configured, 50 products loaded.
- `tank/vms/kitchen-vm@mealie-configured` — Mealie installed, configured, 20 recipes loaded.
- `tank/vms/kitchen-vm@reconciler-v1` — reconciler running and verified.

Snapshots are free until you diverge. They're your insurance.

Within Mealie, run a Settings → Backups → Create backup before each significant config change (different from the ZFS layer; this is Mealie's app-level backup).

Within Grocy, copy `grocy.db` out before each upgrade.

---

## 7. What's deferred to Phase 2

Captured here so they don't get lost:

- MCP wrappers (mealie-mcp, grocy-mcp, reconciler-mcp) behind your existing credential proxy.
- Apprise notifications on low-stock and weekly-list-ready.
- OFF cache service (the Pantry Host idea) for offline-fast barcode metadata.
- Vendor automation: Instacart IDP for Costco/HEB-via-Instacart, Kroger MCP if relevant, Playwright for HEB direct.
- Vendor pack-size rounding in the reconciler.
- Receipt OCR ingestion path.
- Vision-based product lookup ("what's this in the back of the pantry?").
- Substitution rules in the reconciler.
- Patzly grocy-android deployment + on-phone barcode flow polish for the wife.
- iCal export of meal plans (Mealie + Grocy both support this natively, just needs to be wired into your family calendar).
