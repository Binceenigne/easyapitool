#!/usr/bin/env bash
set -euo pipefail

source /etc/easyapitool/easyapitool.env
base_url="http://127.0.0.1:${API_TOOLS_SERVICE_PORT}"
auth_header="Authorization: Bearer ${API_TOOLS_SERVICE_TOKEN}"

printf '%s\n' '--- remove smoke key ---'
/usr/bin/curl -fsS --max-time 5 -X DELETE \
    "$base_url/api/v1/keys/smoke-key" -H "$auth_header" || true
printf '\n%s\n' '--- state after cleanup ---'
/usr/bin/curl -fsS --max-time 5 "$base_url/api/v1/state" -H "$auth_header" \
    | /opt/easyapitool/.venv/bin/python -c 'import json,sys; value=json.load(sys.stdin); print(json.dumps({"keys":len(value.get("keys",[])),"storageMode":value.get("storageMode"),"serverPersistence":value.get("serverPersistence")},ensure_ascii=False))'

printf '%s\n' '--- public domains ---'
for domain in clife.djyx.me api.djyx.me c.djyx.me; do
    printf '%s: ' "$domain"
    /usr/bin/curl -k -sS -I --max-time 5 "https://$domain/" 2>/dev/null \
        | /usr/bin/head -n 1 || printf '%s\n' 'unreachable'
done

printf '%s\n' '--- nginx tls config paths ---'
find /www/server/panel/vhost /www/server/nginx/conf -type f \
    \( -name '*.key' -o -name '*.pem' -o -name '*.crt' \) 2>/dev/null \
    | head -80

printf '%s\n' '--- candidate domain config ---'
grep -RilE 'server_name[[:space:]]+(clife\.djyx\.me|api\.djyx\.me|c\.djyx\.me)' \
    /www/server/nginx/conf /www/server/panel/vhost 2>/dev/null \
    | head -20 | while read -r config; do
        printf '%s\n' "--- $config"
        grep -E 'server_name|listen|ssl_certificate|proxy_pass|root ' "$config" | head -40
    done

printf '%s\n' '--- provider configuration presence ---'
if grep -q '^API_TOOLS_PROVIDER_KEY=' /etc/easyapitool/easyapitool.env 2>/dev/null; then
    printf '%s\n' 'api-tools-provider-key-configured'
else
    printf '%s\n' 'api-tools-provider-key-not-configured'
fi
if grep -qE '^(API_KEY|OPENAI_API_KEY|BASE_URL)=' /root/CLife/.env 2>/dev/null; then
    printf '%s\n' 'existing-clife-provider-config-present'
else
    printf '%s\n' 'existing-clife-provider-config-not-found'
fi