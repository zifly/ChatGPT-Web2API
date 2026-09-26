#!/bin/sh
set -eu
PATH="/usr/local/bin:$PATH"
export PATH
cd "$(dirname "$0")/.."
base() { docker compose -f compose.yaml "$@"; }
desktop() { docker compose -f compose.yaml -f compose.headed.yaml "$@"; }
desktop config --quiet
mkdir -p data/usage
stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup="data/deployment-backups/rest-concurrency-$stamp"
mkdir -p "$backup"
current=$(docker inspect --format '{{.Image}}' chatgpt-web2api)
rollback_image="chatgpt-web2api-rest-rollback:$stamp"
docker image tag "$current" "$rollback_image"
printf '%s\n' "$current" > "$backup/running-image-id.txt"
cat > "$backup/rollback.yaml" <<EOF
services:
  chatgpt-web2api:
    image: $rollback_image
    environment:
      - W2A_REST_POOL_SIZE=1
      - W2A_PARALLEL_TABS=false
EOF
printf '%s\n' "$backup" > data/latest-rest-concurrency-backup.txt
echo "Rollback saved at $backup. Building while the current service stays running."
if ! base build > data/rest-concurrency-build.log 2>&1 || ! desktop build >> data/rest-concurrency-build.log 2>&1; then
    tail -n 50 data/rest-concurrency-build.log
    exit 1
fi
docker run --rm -i --network none --entrypoint python \
    -v "$PWD/src/chatgpt_web2api:/expected:ro" \
    -e W2A_REST_POOL_SIZE=2 -e W2A_PARALLEL_TABS=true -e W2A_TAB_MODE=owned \
    chatgpt-web2api-headed:local - <<'PY'
from pathlib import Path
import chatgpt_web2api
from chatgpt_web2api.api_server import APIServer
from chatgpt_web2api.rest_driver_pool import RestDriverPool
from chatgpt_web2api.config import Config
assert Config.load().server.rest_pool_size == 2
installed = Path(chatgpt_web2api.__file__).parent
for source in Path('/expected').glob('*.py'):
    expected = source.read_bytes().replace(b'\r\n', b'\n')
    assert (installed / source.name).read_bytes().replace(b'\r\n', b'\n') == expected, source.name
print('REST pool image, exact source and configuration verified')
PY
if [ "$(docker inspect --format '{{.Image}}' chatgpt-web2api)" != "$current" ]; then
    echo 'Running image changed during build; refusing to restart it.' >&2
    exit 1
fi
echo 'Recreating the service with two REST workers.'
desktop stop chatgpt-web2api
desktop run --rm --no-deps --entrypoint sh chatgpt-web2api -ec '
for name in SingletonLock SingletonSocket SingletonCookie; do
    lock="/data/chrome-profile/$name"
    if [ -L "$lock" ]; then unlink "$lock";
    elif [ -e "$lock" ]; then echo "Unexpected non-symlink: $lock" >&2; exit 1; fi
done
'
desktop up -d --no-build --force-recreate
if ! docker exec -i chatgpt-web2api python - --wait 120 < scripts/check-nas-api.py; then
    docker logs --tail 200 chatgpt-web2api > "$backup/startup-failure.log" 2>&1 || true
    echo 'Readiness failed; restoring the previous image and singleton mode.' >&2
    sh scripts/rollback-rest-concurrency.sh
    exit 1
fi
docker exec chatgpt-web2api python -c 'import json,urllib.request; h=json.load(urllib.request.urlopen("http://127.0.0.1:8080/health")); p=h["rest_pool"]; assert p["enabled"] and p["size"] == 2; assert all(s["driver_connected"] for s in p["slots"]); print("Both REST workers connected")'
echo 'Deployment ready. Run scripts/check-rest-concurrency.py for opt-in live acceptance.'
echo 'Rollback: sh scripts/rollback-rest-concurrency.sh'
