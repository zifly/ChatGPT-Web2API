#!/bin/sh
# Private evidence only: read container state and logs; never restart or send.
set -eu
cd "$(dirname "$0")/.."
umask 077
mkdir -p data
stamp=$(date -u +%Y%m%dT%H%M%SZ)
out="data/api-error-$stamp.log"
{
    printf 'Collected (UTC): '
    date -u
    docker inspect --format 'Name={{.Name}} Image={{.Config.Image}} ImageID={{.Image}} Started={{.State.StartedAt}} Status={{.State.Status}} RestartCount={{.RestartCount}} OOMKilled={{.State.OOMKilled}}' chatgpt-web2api
    echo '--- Installed code versions (hashes only) ---'
    docker exec -i chatgpt-web2api python - <<'PY'
import hashlib
import json
from importlib.util import find_spec
from pathlib import Path
root = Path(find_spec('chatgpt_web2api').origin).parent
names = ['api_server.py', 'backend_projection.py', 'cdp_driver.py', 'citations.py', 'protocol_reply.py', 'image_upload.py']
print(json.dumps({name: hashlib.sha256((root / name).read_bytes().replace(b'\r\n', b'\n')).hexdigest() if (root / name).exists() else None for name in names}))
PY
    echo '--- Recent six hours of container logs (keep private) ---'
    docker logs --timestamps --since 6h --tail 3000 chatgpt-web2api 2>&1
} > "$out" 2>&1
printf '%s\n' "$out" > data/latest-api-error-path.txt
printf 'Saved %s. No prompt sent; service not restarted.\n' "$out"
