#!/usr/bin/env bash
# Nightly off-box backup: consistent sqlite snapshot -> gzip -> R2 (S3 API).
# Keeps 30 days of dailies remotely; nothing accumulates locally.
# R2_* values come from .env (grepped, not sourced — values contain spaces).
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"

envval() { grep -E "^$1=" .env | head -1 | cut -d= -f2- | tr -d '\r'; }
R2_ACCESS_KEY_ID="$(envval R2_ACCESS_KEY_ID)"
R2_SECRET_ACCESS_KEY="$(envval R2_SECRET_ACCESS_KEY)"
R2_ENDPOINT="$(envval R2_ENDPOINT)"
R2_BUCKET="$(envval R2_BUCKET)"
[ -n "$R2_ACCESS_KEY_ID" ] && [ -n "$R2_SECRET_ACCESS_KEY" ] || { echo "R2 creds missing in .env"; exit 1; }

STAMP="$(date -u +%Y%m%d-%H%M%S)"
TMP="data/backup"
mkdir -p "$TMP"
sqlite3 data/bottski.db ".backup '$TMP/bottski-$STAMP.db'"
gzip -f "$TMP/bottski-$STAMP.db"

export RCLONE_CONFIG_R2_TYPE=s3 \
       RCLONE_CONFIG_R2_PROVIDER=Cloudflare \
       RCLONE_CONFIG_R2_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID" \
       RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY" \
       RCLONE_CONFIG_R2_ENDPOINT="$R2_ENDPOINT"
rclone copyto "$TMP/bottski-$STAMP.db.gz" "r2:$R2_BUCKET/bottski-$STAMP.db.gz"
rclone delete "r2:$R2_BUCKET" --min-age 30d
rm -f "$TMP/bottski-$STAMP.db.gz"
echo "backup uploaded: bottski-$STAMP.db.gz"

base="$(envval HEALTHCHECKS_PING_BASE)"
[ -n "$base" ] && curl -fsS -m 10 --retry 3 "$base/bottski-backup?create=1" >/dev/null || true
