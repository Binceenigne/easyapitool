#!/usr/bin/env bash
set -euo pipefail

stage=/tmp/imggen-deploy
app_root=/opt/imggen-api
web_root=/www/wwwroot/imggen.djyx.me
release_root="$app_root/releases/android"
service_user=imggenapi
service_group=imggenapi

getent group "$service_group" >/dev/null || groupadd --system "$service_group"
id -u "$service_user" >/dev/null 2>&1 || useradd --system --gid "$service_group" \
    --home-dir "$app_root" --shell /usr/sbin/nologin "$service_user"

test -x /opt/easyapitool/.venv/bin/python || {
    echo 'Expected cached Python runtime is unavailable: /opt/easyapitool/.venv/bin/python' >&2
    exit 1
}

rm -rf "$app_root/.venv"
cp -a /opt/easyapitool/.venv "$app_root/.venv"
chmod -R a+rX "$app_root/.venv"

install -d -m 0755 -o root -g root "$app_root" "$release_root" "$web_root" "$web_root/.well-known"
rm -rf "$app_root/backend"
cp -a "$stage/backend" "$app_root/backend"
install -m 0644 "$stage/release/easyapitool-mobile.apk" "$release_root/easyapitool-mobile.apk"
install -m 0644 "$stage/release/manifest.json" "$release_root/manifest.json"
chown -R root:root "$app_root/backend" "$release_root"
chmod -R a+rX "$app_root/backend" "$release_root"

install -d -m 0750 -o "$service_user" -g "$service_group" /etc/imggen-api /run/imggen-api
if [[ ! -f /etc/imggen-api/imggen-api.env ]]; then
    umask 077
    cat > /etc/imggen-api/imggen-api.env <<EOF
API_TOOLS_SERVICE_TOKEN=$(/usr/bin/openssl rand -hex 32)
API_TOOLS_WORK_DIR=/run/imggen-api
API_TOOLS_SERVICE_HOST=127.0.0.1
API_TOOLS_SERVICE_PORT=8766
API_TOOLS_ANDROID_RELEASE_DIR=/opt/imggen-api/releases/android
OPENAI_BASE_URL=https://work.easyclin.cn/v1
EOF
fi
grep -q '^API_TOOLS_ANDROID_RELEASE_DIR=' /etc/imggen-api/imggen-api.env || \
    printf '%s\n' 'API_TOOLS_ANDROID_RELEASE_DIR=/opt/imggen-api/releases/android' >> /etc/imggen-api/imggen-api.env

install -m 0644 "$stage/imggen-api.service" /etc/systemd/system/imggen-api.service
install -m 0755 "$stage/imggen-api-pair" /usr/local/sbin/imggen-api-pair
install -m 0644 "$stage/assetlinks.json" "$web_root/.well-known/assetlinks.json"
install -m 0644 "$stage/imggen-landing.html" "$web_root/index.html"
install -m 0755 "$stage/imggen-cert-renew-hook.sh" /etc/letsencrypt/renewal-hooks/deploy/imggen-nginx-reload

install -m 0644 "$stage/imggen-http.conf" /www/server/panel/vhost/nginx/imggen.djyx.me.conf
/www/server/nginx/sbin/nginx -t -c /www/server/nginx/conf/nginx.conf
/www/server/nginx/sbin/nginx -s reload

if [[ ! -f /etc/letsencrypt/live/imggen.djyx.me/fullchain.pem ]]; then
    /usr/bin/certbot certonly --webroot -w "$web_root" -d imggen.djyx.me \
        --non-interactive --agree-tos --register-unsafely-without-email
fi

install -m 0644 "$stage/imggen-https.conf" /www/server/panel/vhost/nginx/imggen-api-https.conf
/www/server/nginx/sbin/nginx -t -c /www/server/nginx/conf/nginx.conf
/www/server/nginx/sbin/nginx -s reload

systemctl daemon-reload
systemctl enable --now imggen-api.service
systemctl restart imggen-api.service
for attempt in $(seq 1 30); do
    if /usr/bin/curl --fail --silent --show-error --connect-timeout 2 \
        --max-time 5 http://127.0.0.1:8766/readyz >/dev/null 2>&1; then
        exit 0
    fi
    /usr/bin/sleep 1
done
systemctl status imggen-api.service --no-pager -l || true
exit 1