#!/usr/bin/env bash
# Runs one bottski job; on success pings healthchecks if HEALTHCHECKS_PING_BASE
# is set in .env (e.g. https://hc-ping.com/<ping-key>). ?create=1 auto-creates
# the check on first ping, one check per job slug.
set -uo pipefail
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
JOB="${1:?usage: run-job.sh <subcommand>}"
cd "$APP_DIR"

"$HOME/.local/bin/uv" run bottski "$JOB"
rc=$?

if [ $rc -eq 0 ] && [ -f .env ]; then
    base="$(grep -E '^HEALTHCHECKS_PING_BASE=' .env | head -1 | cut -d= -f2- | tr -d '[:space:]')"
    if [ -n "$base" ]; then
        curl -fsS -m 10 --retry 3 "${base}/bottski-${JOB}?create=1" >/dev/null || true
    fi
fi
exit $rc
