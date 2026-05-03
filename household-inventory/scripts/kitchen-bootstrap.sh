#!/usr/bin/env bash
# kitchen-bootstrap.sh — run ON kitchen-vm to set up the household stack.
#
# Prereq: rsync this whole repo into ~/household-inventory on the VM:
#   rsync -av --exclude='.venv' --exclude='.git' \
#     /home/dylan/homelab/household-inventory/ \
#     dylan@kitchen-vm.home.nthparallel.com:~/household-inventory/
#
# Then SSH in and run:
#   bash ~/household-inventory/scripts/kitchen-bootstrap.sh
#
# Idempotent — safe to re-run.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_ROOT="/srv/kitchen"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; RED='\033[0;31m'; NC='\033[0m'
info()    { echo -e "${CYAN}[bootstrap]${NC} $*"; }
success() { echo -e "${GREEN}[bootstrap]${NC} $*"; }
warn()    { echo -e "${YELLOW}[bootstrap]${NC} $*"; }
die()     { echo -e "${RED}[bootstrap] ERROR:${NC} $*" >&2; exit 1; }

[[ "$(hostname)" == kitchen-vm* ]] || warn "Not running on kitchen-vm (hostname: $(hostname)). Continue anyway? Ctrl-C to abort."

# 1. Docker
if ! command -v docker &>/dev/null; then
  info "Installing Docker via get.docker.com ..."
  curl -fsSL https://get.docker.com | sh
  sudo usermod -aG docker "$USER"
  warn "Added $USER to docker group. Log out + back in, then re-run this script."
  exit 0
else
  success "Docker is already installed: $(docker --version)"
fi

# 2. /srv/kitchen tree
if [[ ! -d "$TARGET_ROOT" ]]; then
  info "Creating $TARGET_ROOT ..."
  sudo mkdir -p "$TARGET_ROOT"
  sudo chown -R "$USER:$USER" "$TARGET_ROOT"
fi

# 3. Sync the three stacks. We copy (not symlink) so /srv/kitchen is
#    self-contained for ZFS-snapshot purposes.
for stack in grocy mealie reconciler; do
  src="$REPO_ROOT/infra/$stack"
  dst="$TARGET_ROOT/$stack"
  [[ -d "$src" ]] || die "Source missing: $src"
  info "Syncing $stack -> $dst ..."
  mkdir -p "$dst"
  # Don't clobber data/postgres/.env that may have grown on the VM.
  rsync -a --exclude='data/grocy.db' --exclude='postgres/' --exclude='.env' \
    "$src/" "$dst/"
done

# 4. .env files
if [[ ! -f "$TARGET_ROOT/mealie/.env" ]]; then
  info "Generating Mealie DB password ..."
  # hex (not base64) so the password doesn't contain '/', '+', or '=' which
  # break URL-encoded connection strings in some clients.
  printf "MEALIE_DB_PASSWORD=%s\n" "$(openssl rand -hex 32)" \
    > "$TARGET_ROOT/mealie/.env"
  success "Wrote $TARGET_ROOT/mealie/.env"
fi

if [[ ! -f "$TARGET_ROOT/reconciler/.env" ]]; then
  cp "$TARGET_ROOT/reconciler/.env.example" "$TARGET_ROOT/reconciler/.env"
  warn "Created $TARGET_ROOT/reconciler/.env from example."
  warn "Edit it later with real GROCY_API_KEY and MEALIE_API_TOKEN once those exist."
fi

# 5. Bring up the foundational stacks (grocy + mealie). The reconciler
#    needs API keys from the web UIs first, so don't auto-start it.
info "Bringing up Grocy ..."
( cd "$TARGET_ROOT/grocy" && docker compose up -d )

info "Bringing up Mealie ..."
( cd "$TARGET_ROOT/mealie" && docker compose up -d )

# 6. Wait for ports.
info "Waiting for Grocy on :9283 ..."
for _ in {1..30}; do curl -sSf http://localhost:9283/ >/dev/null && break; sleep 2; done
info "Waiting for Mealie on :9925 ..."
for _ in {1..60}; do curl -sSf http://localhost:9925/api/app/about >/dev/null && break; sleep 2; done

success "Grocy + Mealie are up."
echo ""
warn "Reconciler stack is staged but NOT started. Next steps:"
warn "  1. Open https://grocy.home.nthparallel.com — admin/admin → change pwd → create API key for a 'reconciler' user."
warn "  2. Open https://mealie.home.nthparallel.com — finish setup wizard → Profile → Manage API Tokens → create 'reconciler' token."
warn "  3. Edit $TARGET_ROOT/reconciler/.env with both values."
warn "  4. cd $TARGET_ROOT/reconciler && docker compose up -d"
warn "  5. Verify: curl https://kitchen-api.home.nthparallel.com/healthz"
echo ""
