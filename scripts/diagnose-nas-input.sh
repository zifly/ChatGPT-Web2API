#!/bin/sh
# Private evidence only. This script never restarts the service or sends a prompt.
set -eu
cd "$(dirname "$0")/.."
umask 077
mkdir -p data
out=data/input-error-diagnostic.log
{
    date -u
    docker inspect --format 'Image={{.Config.Image}} ImageID={{.Image}} Created={{.Created}} Started={{.State.StartedAt}}' chatgpt-web2api
    docker exec -i chatgpt-web2api python - < scripts/diagnose-nas-input.py || true
    echo '--- Reported failures: 2026-09-20 12:55-13:05 UTC (keep private) ---'
    docker logs --since 2026-09-20T12:55:00Z --until 2026-09-20T13:05:00Z --tail 600 chatgpt-web2api 2>&1
    echo '--- Recent server log (keep this file private) ---'
    docker logs --since 15m --tail 150 chatgpt-web2api 2>&1
} > "$out" 2>&1
echo "Saved $out. No prompt sent; service not restarted."
