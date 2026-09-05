#!/usr/bin/env bash
set -euo pipefail

pair_output="$(/usr/local/sbin/easyapitool-pair)"
pairing_code="${pair_output##*: }"
base_url="https://clife.djyx.me/easyapitool-api"

wrong_status="$(/usr/bin/curl --proto '=https' --tlsv1.2 -sS -o /dev/null -w '%{http_code}' \
    --max-time 10 -X POST "$base_url/api/v1/pair" \
    -H 'Content-Type: application/json' \
    --data '{"pairingCode":"00000000000000000000000000000000"}')"

pair_response="$(/usr/bin/curl --proto '=https' --tlsv1.2 -fsS --max-time 10 \
    -X POST "$base_url/api/v1/pair" \
    -H 'Content-Type: application/json' \
    --data "{\"pairingCode\":\"$pairing_code\"}")"

token_present="$(printf '%s' "$pair_response" | /opt/easyapitool/.venv/bin/python -c 'import json,sys; print(str(bool(json.load(sys.stdin).get("bearerToken"))).lower())')"
replay_status="$(/usr/bin/curl --proto '=https' --tlsv1.2 -sS -o /dev/null -w '%{http_code}' \
    --max-time 10 -X POST "$base_url/api/v1/pair" \
    -H 'Content-Type: application/json' \
    --data "{\"pairingCode\":\"$pairing_code\"}")"

test ! -e /run/easyapitool/pairing-code
printf 'wrong=%s token_present=%s replay=%s consumed=true\n' "$wrong_status" "$token_present" "$replay_status"