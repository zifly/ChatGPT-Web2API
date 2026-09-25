#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
if docker compose version >/dev/null 2>&1; then
    base() { docker compose -f compose.yaml "$@"; }
    desktop() { docker compose -f compose.yaml -f compose.headed.yaml "$@"; }
else
    base() { docker-compose -f compose.yaml "$@"; }
    desktop() { docker-compose -f compose.yaml -f compose.headed.yaml "$@"; }
fi
if [ ! -s data/vnc-password.txt ]; then
    echo "Missing data/vnc-password.txt" >&2
    exit 1
fi
desktop config --quiet
echo "Installing browser desktop; build output is in desktop-build.log..."
if ! base build > desktop-build.log 2>&1 || ! desktop build >> desktop-build.log 2>&1; then
    tail -n 60 desktop-build.log
    exit 1
fi
base stop chatgpt-web2api
base run --rm --no-deps --entrypoint /bin/sh chatgpt-web2api -c '
set -eu
for name in SingletonLock SingletonSocket SingletonCookie; do
    lock="/data/chrome-profile/$name"
    if [ -L "$lock" ]; then unlink "$lock"; fi
done
'
desktop up -d --no-build
sleep 15
desktop logs --tail=100 --no-color > runtime.log 2>&1
echo "Open http://NAS-IP:6080/vnc.html and use the password in data/vnc-password.txt"
