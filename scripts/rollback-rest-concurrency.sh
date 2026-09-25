#!/bin/sh
set -eu
PATH="/usr/local/bin:$PATH"
export PATH
cd "$(dirname "$0")/.."
backup=$(cat data/latest-rest-concurrency-backup.txt)
case "$backup" in
    data/deployment-backups/rest-concurrency-*) ;;
    *) echo 'Invalid REST rollback path' >&2; exit 1 ;;
esac
test -f "$backup/rollback.yaml"
docker compose -f compose.yaml -f compose.headed.yaml -f "$backup/rollback.yaml" stop chatgpt-web2api
docker compose -f compose.yaml -f compose.headed.yaml -f "$backup/rollback.yaml" run --rm --no-deps --entrypoint sh chatgpt-web2api -ec '
for name in SingletonLock SingletonSocket SingletonCookie; do
    lock="/data/chrome-profile/$name"
    if [ -L "$lock" ]; then unlink "$lock";
    elif [ -e "$lock" ]; then echo "Unexpected non-symlink: $lock" >&2; exit 1; fi
done
'
docker compose -f compose.yaml -f compose.headed.yaml -f "$backup/rollback.yaml" up -d --no-build --force-recreate
docker exec -i chatgpt-web2api python - --wait 120 < scripts/check-nas-api.py
echo 'Previous image restored in singleton mode; conversation IDs remain valid.'
