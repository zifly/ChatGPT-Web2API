#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
if docker compose version >/dev/null 2>&1; then
    compose() { docker compose "$@"; }
else
    compose() { docker-compose "$@"; }
fi

compose stop chatgpt-web2api
compose run --rm --no-deps --entrypoint /bin/sh chatgpt-web2api -c '
set -eu
for name in SingletonLock SingletonSocket SingletonCookie; do
    lock="/data/chrome-profile/$name"
    if [ -L "$lock" ]; then
        unlink "$lock"
        echo "Removed stale link: $name"
    elif [ -e "$lock" ]; then
        echo "Unexpected non-symlink: $lock; stopping without deleting it" >&2
        exit 1
    fi
done
'
compose up -d
echo "Waiting 40 seconds for Chrome and cookie import..."
sleep 40
compose logs --tail=160 --no-color > runtime.log 2>&1
compose ps -a >> runtime.log 2>&1
echo "Saved runtime.log"
