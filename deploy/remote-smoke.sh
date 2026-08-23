#!/usr/bin/env bash
set -euo pipefail

source /etc/easyapitool/easyapitool.env
base_url="${EASYAPITOOL_SMOKE_BASE_URL:-http://127.0.0.1:${API_TOOLS_SERVICE_PORT}}"
curl_args=()
if [[ "$base_url" == https://* ]]; then
    curl_args+=(--proto '=https' --tlsv1.2)
fi
auth_header="Authorization: Bearer ${API_TOOLS_SERVICE_TOKEN}"
work_dir="$(mktemp -d /tmp/easyapitool-smoke.XXXXXX)"
trap 'rm -rf "$work_dir"' EXIT

printf 'health='
/usr/bin/curl "${curl_args[@]}" -fsS --max-time 5 "$base_url/healthz"
printf '\nready='
/usr/bin/curl "${curl_args[@]}" -fsS --max-time 5 "$base_url/readyz"
printf '\nunauthorized_status='
/usr/bin/curl "${curl_args[@]}" -sS -o /dev/null -w '%{http_code}' --max-time 5 "$base_url/api/v1/state"

printf '\ncors='
/usr/bin/curl "${curl_args[@]}" -sS -D "$work_dir/cors.headers" -o /dev/null -X OPTIONS --max-time 5 \
    "$base_url/api/v1/state" \
    -H 'Origin: capacitor://localhost' \
    -H 'Access-Control-Request-Method: GET' \
    -H 'Access-Control-Request-Headers: authorization'
grep -Eio 'HTTP/[^ ]+ [0-9]+|access-control-allow-origin: [^[:space:]]+|access-control-allow-methods: [^\r]+' "$work_dir/cors.headers" | tr '\n' ';'

printf '\nstate='
/usr/bin/curl "${curl_args[@]}" -fsS --max-time 5 "$base_url/api/v1/state" -H "$auth_header" \
    | python3 -c 'import json,sys; value=json.load(sys.stdin); print(json.dumps({"keys":len(value.get("keys",[])),"storageMode":value.get("storageMode"),"serverPersistence":value.get("serverPersistence")},ensure_ascii=False))'

/opt/easyapitool/.venv/bin/python - "$work_dir/test.png" <<'PY'
from pathlib import Path
from PIL import Image
import sys
Image.new("RGB", (8, 8), (12, 34, 56)).save(Path(sys.argv[1]), "PNG")
PY

printf '\nupload='
/usr/bin/curl "${curl_args[@]}" -fsS --max-time 10 -F "file=@$work_dir/test.png;type=image/png" \
    -H "$auth_header" "$base_url/api/v1/uploads/references" > "$work_dir/upload.json"
/opt/easyapitool/.venv/bin/python - "$work_dir/upload.json" "$work_dir/resource.id" <<'PY'
import json
import sys
from pathlib import Path
payload = json.loads(Path(sys.argv[1]).read_text())
resource = payload["resource"]
print(json.dumps({
    "ok": payload.get("ok"),
    "resourceId": bool(resource.get("resourceId")),
    "previewResourceId": bool(resource.get("previewResourceId")),
    "downloadUrl": resource.get("downloadUrl"),
}, ensure_ascii=False))
Path(sys.argv[2]).write_text(resource["resourceId"])
PY

resource_id="$(cat "$work_dir/resource.id")"
printf 'download='
/usr/bin/curl "${curl_args[@]}" -fsS --max-time 10 -o "$work_dir/downloaded.png" \
    -w '%{http_code} %{size_download}' "$base_url/api/v1/assets/$resource_id" \
    -H "$auth_header"

printf '\nsse='
timeout 4 /usr/bin/curl "${curl_args[@]}" -sS --max-time 5 -N "$base_url/api/v1/events?cursor=0" \
    -H "$auth_header" | head -n 3 | tr '\n' ';' || true
printf '\n'