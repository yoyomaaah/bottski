#!/usr/bin/env bash
# bottski droplet provisioning. Run as root on a fresh Ubuntu 24.04 droplet:
#   curl -fsSL https://raw.githubusercontent.com/yoyomaaah/bottski/main/deploy/provision.sh | bash
# Idempotent: safe to re-run (also serves as the update path).
set -euo pipefail

REPO="https://github.com/yoyomaaah/bottski.git"
HOME_DIR=/home/bottski
APP_DIR=$HOME_DIR/bottski

echo "== packages =="
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -yq git curl sqlite3 unattended-upgrades
timedatectl set-timezone UTC

echo "== user =="
id -u bottski &>/dev/null || useradd -m -s /bin/bash bottski

echo "== uv =="
[ -x "$HOME_DIR/.local/bin/uv" ] || sudo -u bottski bash -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'

echo "== code =="
if [ -d "$APP_DIR/.git" ]; then
    sudo -u bottski git -C "$APP_DIR" pull --ff-only
else
    sudo -u bottski git clone "$REPO" "$APP_DIR"
fi
sudo -u bottski bash -c "cd $APP_DIR && $HOME_DIR/.local/bin/uv sync"

echo "== systemd =="
cp "$APP_DIR"/deploy/systemd/*.service "$APP_DIR"/deploy/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now bottski-collect.timer bottski-observe.timer bottski-backfill.timer bottski-report.timer bottski-decide.timer

echo "== status =="
if [ -f "$APP_DIR/.env" ]; then
    echo "OK: .env present"
else
    cat <<'MSG'
!! No .env yet — collection will skip/fail until it exists.
   From your machine:  scp .env root@<droplet-ip>:/home/bottski/bottski/.env
   Then:               chown bottski:bottski /home/bottski/bottski/.env && chmod 600 /home/bottski/bottski/.env
MSG
fi
systemctl list-timers bottski-collect.timer --no-pager || true
echo "provisioning done"
