#!/usr/bin/env bash
# Server-side deploy for s2change.dzaky.ai.id (run on the VPS from the repo root).
# Pull, sync Python deps, publish the committed results into local PostGIS, build the viewer,
# (re)start the API under PM2. Secrets live in ./.env (never committed).
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
APP=s2change-api
PORT=8310
export PATH="$HOME/.local/bin:$PATH"
log() { echo "[$(date '+%H:%M:%S')] $*"; }

cd "$REPO"
[ -f .env ] || { echo "missing $REPO/.env (see app/.env.example)"; exit 1; }
chmod 600 .env

log "git pull"
git fetch origin && git reset --hard origin/main

log "uv sync (app extra)"
uv sync --frozen --no-dev --extra app

log "publish results into PostGIS (idempotent)"
uv run --no-sync python -m app.publish

log "build viewer"
(cd viewer && npm ci --no-audit --no-fund && npm run build:site)

log "restart $APP"
if pm2 describe "$APP" >/dev/null 2>&1; then
  pm2 restart "$APP" --update-env
else
  pm2 start "$REPO/.venv/bin/uvicorn" --name "$APP" --cwd "$REPO" -- \
    app.main:app --host 127.0.0.1 --port "$PORT" --workers 1 --proxy-headers --forwarded-allow-ips 127.0.0.1
  pm2 save
fi
sleep 3
curl -fsS "http://127.0.0.1:$PORT/api/health" && echo && log "deploy OK"
