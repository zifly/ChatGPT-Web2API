#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
docker exec -i chatgpt-web2api python - < scripts/diagnose-nas-auth.py > auth-diagnostic.log 2>&1
docker logs --since 3m --tail 100 chatgpt-web2api >> auth-diagnostic.log 2>&1
echo "Saved auth-diagnostic.log"
