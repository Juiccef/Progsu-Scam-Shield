#!/usr/bin/env bash
# Sets up Shield on a fresh Ubuntu 24.04 server. tools/vultr_deploy.py copies the code and
# .env to /opt/shield and then runs this there as root. Safe to run again.
#
#   bash /opt/shield/deploy/vultr-setup.sh <public host>
#
# Shield runs as one uvicorn process on 127.0.0.1:8000 (all call state is in memory, so
# never more than one). Caddy answers on 443, gets the certificate for <public host> by
# itself, and passes webhooks, the audio WebSocket and the event feed through.
set -euo pipefail

HOST="${1:?usage: vultr-setup.sh <public host>}"
APP=/opt/shield

echo "== waiting for the server's first boot to finish"
cloud-init status --wait >/dev/null 2>&1 || true

echo "== system packages (Python virtualenv support, Caddy)"
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a
# A new server runs its own package updates for a while, and those hold apt's locks.
apt_get() {
  local attempt
  for attempt in 1 2 3 4 5 6; do
    apt-get -o DPkg::Lock::Timeout=300 -qq "$@" && return 0
    sleep 10
  done
  return 1
}
apt_get update
apt_get -y install python3-venv caddy curl >/dev/null

echo "== Python packages"
id shield >/dev/null 2>&1 || useradd --system --home-dir "$APP" --shell /usr/sbin/nologin shield
[ -x "$APP/.venv/bin/python" ] || python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install --quiet --disable-pip-version-check -r "$APP/requirements.txt"
mkdir -p "$APP/logs"
chown -R shield:shield "$APP"
chmod 600 "$APP/.env"

echo "== Shield service"
cat > /etc/systemd/system/shield.service <<EOF
[Unit]
Description=Shield scam protection backend
After=network-online.target
Wants=network-online.target

[Service]
User=shield
WorkingDirectory=$APP
ExecStart=$APP/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000 --timeout-graceful-shutdown 2
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --quiet shield
systemctl restart shield

echo "== HTTPS for $HOST"
cat > /etc/caddy/Caddyfile <<EOF
$HOST {
	reverse_proxy 127.0.0.1:8000
}
EOF
# Vultr's Ubuntu images come with ufw on and only SSH open.
if command -v ufw >/dev/null 2>&1; then
  ufw allow 80/tcp >/dev/null
  ufw allow 443/tcp >/dev/null
fi
systemctl enable --quiet caddy
systemctl reload caddy 2>/dev/null || systemctl restart caddy

echo "== checking that Shield answers"
if ! curl -fsS --retry 15 --retry-delay 1 --retry-connrefused -o /dev/null http://127.0.0.1:8000/health; then
  echo "Shield did not start. Its log:" >&2
  journalctl -u shield -n 40 --no-pager >&2
  exit 1
fi
echo "== done"
