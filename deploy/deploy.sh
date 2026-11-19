#!/usr/bin/env bash
# Server-side deploy for s2change.dzaky.ai.id (run on the VPS from the repo root).
# Pull, sync Python deps, build the viewer, (re)start the API under PM2.
# The database is Supabase PostGIS; results are published from a workstation with the owner
# credentials (`python -m app.publish`), so this server only holds the read-only s2cd_reader URL.
# Secrets live in ./.env (never committed).
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
APP=s2change-api
PORT=8310
export PATH="$HOME/.local/bin:$PATH"
log() { echo "[$(date '+%H:%M:%S')] $*"; }

cd "$REPO"
[ -f .env ] || { echo "missing $REPO/.env (see app/.env.example)"; exit 1; }
chmod 600 .env

# Pull first, then re-exec so the rest of the run uses the freshly pulled copy of this script
# (bash reads scripts incrementally; a reset mid-run would otherwise execute the old version).
if [ -z "${S2CD_DEPLOY_REEXEC:-}" ]; then
  log "git pull"
  git fetch origin && git reset --hard origin/main
  S2CD_DEPLOY_REEXEC=1 exec bash "$REPO/deploy/deploy.sh" "$@"
fi

log "uv sync (app extra)"
uv sync --frozen --no-dev --extra app

log "build viewer"
(cd viewer && npm ci --no-audit --no-fund && npm run build:site)

log "restart $APP"
if pm2 describe "$APP" >/dev/null 2>&1; then
  pm2 restart "$APP" --update-env
else
  pm2 start "$REPO/.venv/bin/uvicorn" --name "$APP" --cwd "$REPO" --interpreter none -- \
    app.main:app --host 127.0.0.1 --port "$PORT" --workers 1 --proxy-headers --forwarded-allow-ips 127.0.0.1
  pm2 save
fi
for _ in $(seq 1 20); do curl -fsS -m 3 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 && break; sleep 3; done
curl -fsS "http://127.0.0.1:$PORT/api/health" && echo && log "deploy OK"
