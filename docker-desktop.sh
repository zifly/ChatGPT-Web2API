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
# Docker restarts retain /tmp, but the old X server process is gone.
# Never remove a lock belonging to a still-running Xvfb process.
if [ -f /tmp/.X99-lock ]; then
    xpid=$(tr -d '[:space:]' < /tmp/.X99-lock)
    case "$xpid" in
        ''|*[!0-9]*) ;;
        *) if kill -0 "$xpid" 2>/dev/null && [ "$(cat "/proc/$xpid/comm" 2>/dev/null)" = Xvfb ]; then
               echo "Display :99 still belongs to a live Xvfb process" >&2
               exit 1
           fi ;;
    esac
fi
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99
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
