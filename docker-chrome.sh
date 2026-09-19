#!/bin/sh
set -eu

# The image runs as root; Chrome requires this flag in that configuration.
# Chrome needs its own proxy flag; HTTP_PROXY alone does not configure it.
if [ -n "${W2A_CHROME_PROXY:-}" ]; then
    set -- "--proxy-server=$W2A_CHROME_PROXY" "$@"
fi
exec /usr/bin/google-chrome --no-sandbox --disable-dev-shm-usage "$@"
