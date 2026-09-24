#!/bin/bash
set -euxo pipefail

DOMAIN="__DOMAIN__"
TOKEN_PARAM="__TOKEN_PARAM__"
BIND_PORT="__BIND_PORT__"
VHOST_HTTP_PORT="__VHOST_HTTP_PORT__"
REGION="__REGION__"
FRP_VERSION="__FRP_VERSION__"
CADDY_VERSION="__CADDY_VERSION__"
ALLOW_PORTS="__ALLOW_PORTS__"

# t4g.nano has 0.5 GiB RAM and no swap by default, which isn't enough for
# dnf/pip transactions on a fresh box (dnf gets OOM-killed without this).
fallocate -l 1G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab

dnf install -y python3-pip tar gzip
pip3 install --no-cache-dir certbot certbot-dns-route53

# --- frps ---
curl -fsSL -o /tmp/frp.tar.gz \
  "https://github.com/fatedier/frp/releases/download/v${FRP_VERSION}/frp_${FRP_VERSION}_linux_arm64.tar.gz"
mkdir -p /opt/frp
tar -xzf /tmp/frp.tar.gz -C /opt/frp --strip-components=1
rm -f /tmp/frp.tar.gz

TOKEN=$(aws ssm get-parameter --name "${TOKEN_PARAM}" --with-decryption --region "${REGION}" --query Parameter.Value --output text)

cat > /opt/frp/frps.toml <<EOF
bindPort = ${BIND_PORT}
auth.method = "token"
auth.token = "${TOKEN}"
vhostHTTPPort = ${VHOST_HTTP_PORT}
EOF
if [[ -n "${ALLOW_PORTS}" ]]; then
  echo "${ALLOW_PORTS}" >> /opt/frp/frps.toml
fi
chmod 600 /opt/frp/frps.toml

cat > /etc/systemd/system/frps.service <<'UNIT'
[Unit]
Description=frp server
After=network.target

[Service]
ExecStart=/opt/frp/frps -c /opt/frp/frps.toml
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now frps

# --- caddy (reverse proxy + TLS termination) ---
curl -fsSL -o /tmp/caddy.tar.gz \
  "https://github.com/caddyserver/caddy/releases/download/v${CADDY_VERSION}/caddy_${CADDY_VERSION}_linux_arm64.tar.gz"
tar -xzf /tmp/caddy.tar.gz -C /usr/local/bin caddy
chmod +x /usr/local/bin/caddy
rm -f /tmp/caddy.tar.gz

# --- wildcard TLS cert via Let's Encrypt DNS-01 (Route53) ---
certbot certonly --non-interactive --agree-tos --register-unsafely-without-email \
  --dns-route53 -d "${DOMAIN}" -d "*.${DOMAIN}"

mkdir -p /etc/caddy
cat > /etc/caddy/Caddyfile <<EOF
*.${DOMAIN} {
    tls /etc/letsencrypt/live/${DOMAIN}/fullchain.pem /etc/letsencrypt/live/${DOMAIN}/privkey.pem
    reverse_proxy 127.0.0.1:${VHOST_HTTP_PORT}
}

http://*.${DOMAIN} {
    redir https://{host}{uri} permanent
}
EOF

cat > /etc/systemd/system/caddy.service <<'UNIT'
[Unit]
Description=Caddy
After=network.target

[Service]
ExecStart=/usr/local/bin/caddy run --config /etc/caddy/Caddyfile
ExecReload=/usr/local/bin/caddy reload --config /etc/caddy/Caddyfile
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now caddy

# --- certbot renewal, twice daily, reloads caddy on success ---
cat > /etc/systemd/system/certbot-renew.service <<'UNIT'
[Unit]
Description=certbot renew

[Service]
Type=oneshot
ExecStart=/usr/local/bin/certbot renew --quiet --deploy-hook "systemctl reload caddy"
UNIT

cat > /etc/systemd/system/certbot-renew.timer <<'UNIT'
[Unit]
Description=Twice daily certbot renewal check

[Timer]
OnCalendar=*-*-* 00,12:00:00
Persistent=true

[Install]
WantedBy=timers.target
UNIT

systemctl daemon-reload
systemctl enable --now certbot-renew.timer
