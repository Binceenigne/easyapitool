#!/usr/bin/env bash
set -euo pipefail

web_root="/www/wwwroot/clife.djyx.me"
challenge_dir="$web_root/.well-known/acme-challenge"
challenge_file="$challenge_dir/easyapitool-preflight"
expected="easyapitool-acme-preflight"

install -d -m 0755 "$challenge_dir"
printf '%s\n' "$expected" > "$challenge_file"
trap 'rm -f "$challenge_file"' EXIT

printf 'local='
/usr/bin/curl -fsS --max-time 10 -H 'Host: clife.djyx.me' \
    http://127.0.0.1/.well-known/acme-challenge/easyapitool-preflight
printf '\npublic='
/usr/bin/curl -fsS --max-time 10 \
    http://clife.djyx.me/.well-known/acme-challenge/easyapitool-preflight
printf '\n'