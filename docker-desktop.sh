#!/bin/bash
set -e
export DISPLAY=:99
export W2A_HEADLESS=false
if [ ! -s /run/secrets/vnc-password ]; then
    echo "Missing VNC password file" >&2
    exit 1
fi
umask 077
x11vnc -storepasswd "$(cat /run/secrets/vnc-password)" /tmp/vnc.pass >/dev/null 2>&1
Xvfb :99 -screen 0 1440x900x24 -nolisten tcp &
for i in $(seq 1 30); do
    if xdpyinfo -display :99 >/dev/null 2>&1; then break; fi
    sleep 1
done
xdpyinfo -display :99 >/dev/null 2>&1
fluxbox >/tmp/fluxbox.log 2>&1 &
x11vnc -display :99 -localhost -rfbport 5900 -rfbauth /tmp/vnc.pass -forever -shared -o /tmp/x11vnc.log &
websockify --web=/usr/share/novnc 6080 127.0.0.1:5900 &
echo "Browser desktop ready on port 6080 (VNC password required)"
exec /docker-entrypoint.sh "$@"
