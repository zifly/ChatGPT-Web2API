#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
if docker compose version >/dev/null 2>&1; then
    compose() { docker compose "$@"; }
else
    compose() { docker-compose "$@"; }
fi

# Stop the restart loop so the diagnostic is the sole profile owner.
compose stop chatgpt-web2api
compose run --rm --no-deps --entrypoint /bin/sh chatgpt-web2api -c '
set -u
echo "=== Chrome version ==="
/usr/bin/google-chrome --version
echo "=== Profile directory and lock targets ==="
ls -ld /data/chrome-profile
for name in SingletonLock SingletonSocket SingletonCookie; do
    if [ -L "/data/chrome-profile/$name" ]; then
        printf "%s -> " "$name"
        readlink "/data/chrome-profile/$name"
    fi
done
echo "=== Existing profile startup (15 second limit) ==="
timeout 15s /usr/local/bin/docker-chrome --headless=new --no-first-run --no-default-browser-check --remote-debugging-port=9222 --user-data-dir=/data/chrome-profile about:blank
echo "Chrome exit code: $? (124 means the diagnostic time limit was reached)"
echo "=== Fresh temporary profile startup (15 second limit) ==="
probe_dir=$(mktemp -d /tmp/w2a-chrome-probe.XXXXXX)
timeout 15s /usr/local/bin/docker-chrome --headless=new --no-first-run --no-default-browser-check --remote-debugging-port=9223 --user-data-dir="$probe_dir" about:blank
echo "Chrome exit code: $? (124 means the diagnostic time limit was reached)"
' > chrome-diagnostic.log 2>&1
echo "Saved chrome-diagnostic.log. Service remains stopped pending diagnosis."
