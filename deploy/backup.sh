#!/usr/bin/env bash
# Nightly off-box backup: consistent sqlite snapshot -> gzip -> R2 via curl's
# native SigV4 (Ubuntu's packaged rclone mis-signs against R2; curl works).
# One object per day (bottski-YYYYMMDD.db.gz); prunes the 31-day-old object by
# name, so the token needs no list permission. R2_* values grepped from .env.
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"

envval() { grep -E "^$1=" .env | head -1 | cut -d= -f2- | tr -d '\r'; }
AK="$(envval R2_ACCESS_KEY_ID)"
SK="$(envval R2_SECRET_ACCESS_KEY)"
EP="$(envval R2_ENDPOINT)"
BUCKET="$(envval R2_BUCKET)"
[ -n "$AK" ] && [ -n "$SK" ] && [ -n "$EP" ] && [ -n "$BUCKET" ] || {
    echo "R2 config missing in .env"; exit 1; }

TODAY="$(date -u +%Y%m%d)"
PRUNE="$(date -u -d "31 days ago" +%Y%m%d)"
TMP="data/backup"
mkdir -p "$TMP"
sqlite3 data/bottski.db ".backup '$TMP/bottski-$TODAY.db'"
gzip -f "$TMP/bottski-$TODAY.db"

code=$(curl -s -o /dev/null -w "%{http_code}" --retry 3 \
    --aws-sigv4 "aws:amz:auto:s3" --user "$AK:$SK" \
    -T "$TMP/bottski-$TODAY.db.gz" "$EP/$BUCKET/bottski-$TODAY.db.gz")
[ "$code" = "200" ] || { echo "upload failed: HTTP $code"; exit 1; }

# prune the object that just aged out (404 is fine — nothing there yet)
curl -s -o /dev/null --aws-sigv4 "aws:amz:auto:s3" --user "$AK:$SK" \
    -X DELETE "$EP/$BUCKET/bottski-$PRUNE.db.gz" || true
rm -f "$TMP/bottski-$TODAY.db.gz"
echo "backup uploaded: bottski-$TODAY.db.gz"

base="$(envval HEALTHCHECKS_PING_BASE)"
[ -n "$base" ] && curl -fsS -m 10 --retry 3 "$base/bottski-backup?create=1" >/dev/null || true
