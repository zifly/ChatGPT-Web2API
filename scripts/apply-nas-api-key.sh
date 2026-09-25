#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
if docker compose version >/dev/null 2>&1; then
    compose() { docker compose -f compose.yaml -f compose.headed.yaml "$@"; }
else
    compose() { docker-compose -f compose.yaml -f compose.headed.yaml "$@"; }
fi
compose config --quiet
compose stop chatgpt-web2api
compose run --rm --no-deps --entrypoint /bin/sh chatgpt-web2api -c '
set -eu
for name in SingletonLock SingletonSocket SingletonCookie; do
    lock="/data/chrome-profile/$name"
    if [ -L "$lock" ]; then unlink "$lock"; fi
done
'
compose up -d --no-build
sleep 40
compose logs --tail=100 --no-color > runtime.log 2>&1
compose ps -a >> runtime.log 2>&1
echo "Saved runtime.log. API key is in data/api.env."
