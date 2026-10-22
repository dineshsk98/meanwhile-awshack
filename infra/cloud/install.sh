#!/bin/bash
# Sets up the Meanwhile cloud box (an Ubuntu Deep Learning GPU AMI) after the code is copied: system packages, Python venvs,
# Ollama with its models, Caddy, settings files with placeholders, and the systemd units.
# What it installs and from where is read from /etc/meanwhile/install.env (values in install.env.example).
# Run on the box as ubuntu: MEANWHILE_PUBLIC_HOST=<host> bash ~/meanwhile/infra/cloud/install.sh
# (safe to run again; keeps existing settings). The host is the box's public name, e.g. meanwhile.example.com.
set -euo pipefail

if ! [[ "${MEANWHILE_PUBLIC_HOST:-}" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]]; then
  echo "MEANWHILE_PUBLIC_HOST must be set to the box's public host name (e.g. meanwhile.example.com)" >&2
  exit 1
fi
HOST="$MEANWHILE_PUBLIC_HOST"

HERE="$(cd "$(dirname "$0")" && pwd)"
CORE="$(cd "$HERE/../.." && pwd)"
VOICE="$CORE/voice"

# Prints a step heading.
step() { printf '\n== %s\n' "$1"; }

step "Install settings (/etc/meanwhile/install.env)"
sudo mkdir -p /etc/meanwhile
[ -f /etc/meanwhile/install.env ] || sudo install -m 644 "$HERE/install.env.example" /etc/meanwhile/install.env
# shellcheck disable=SC1091
. /etc/meanwhile/install.env

step "System packages"
sudo apt-get update -q
# shellcheck disable=SC2086
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q $MEANWHILE_APT_PACKAGES
df -h / | tail -1
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader

step "Python venvs ($MEANWHILE_PYTHON)"
command -v uv >/dev/null || curl -LsSf "$MEANWHILE_UV_INSTALL_URL" | sh
export PATH="$HOME/.local/bin:$PATH"
uv venv -q --allow-existing -p "$MEANWHILE_PYTHON" "$CORE/.venv"
uv pip install -q -p "$CORE/.venv/bin/python" -r "$CORE/requirements.txt"
uv venv -q --allow-existing -p "$MEANWHILE_PYTHON" "$VOICE/.venv"
uv pip install -q -p "$VOICE/.venv/bin/python" -r "$VOICE/backend/requirements.txt"
"$CORE/.venv/bin/python" -c "import torch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available())"

step "Ollama ($MEANWHILE_OLLAMA_MODELS, local only, kept loaded)"
command -v ollama >/dev/null || curl -fsSL "$MEANWHILE_OLLAMA_INSTALL_URL" | sh
sudo mkdir -p /etc/systemd/system/ollama.service.d
printf '[Service]\nEnvironment=OLLAMA_HOST=%s OLLAMA_KEEP_ALIVE=%s\n' "$MEANWHILE_OLLAMA_HOST" "$MEANWHILE_OLLAMA_KEEP_ALIVE" | sudo tee /etc/systemd/system/ollama.service.d/meanwhile.conf >/dev/null
sudo systemctl daemon-reload
sudo systemctl restart ollama
sleep 3
for model in $MEANWHILE_OLLAMA_MODELS; do ollama pull "$model"; done

step "Caddy"
if [ ! -x /usr/local/bin/caddy ]; then
  sudo curl -fsSL --retry 6 --retry-all-errors --retry-delay 3 "$MEANWHILE_CADDY_DOWNLOAD_URL" -o /usr/local/bin/caddy
  sudo chmod +x /usr/local/bin/caddy
fi
id caddy >/dev/null 2>&1 || sudo useradd --system --home /var/lib/caddy --shell /usr/sbin/nologin caddy
sudo mkdir -p /etc/caddy /var/lib/caddy
sudo chown caddy:caddy /var/lib/caddy
sed "s/__HOST__/$HOST/g" "$HERE/Caddyfile.tmpl" | sudo tee /etc/caddy/Caddyfile >/dev/null

step "Settings in /etc/meanwhile (placeholders only; values are never printed)"
if [ ! -f /etc/meanwhile/key.env ]; then
  echo "MEANWHILE_KEY=$(head -c 64 /dev/urandom | base64 | tr -dc A-Za-z0-9 | head -c 40)" | sudo tee /etc/meanwhile/key.env >/dev/null
fi
for name in core voice; do
  [ -f "/etc/meanwhile/$name.env" ] || sed "s/__HOST__/$HOST/g" "$HERE/$name.env.example" | sudo tee "/etc/meanwhile/$name.env" >/dev/null
done
sudo chown root:root /etc/meanwhile/key.env /etc/meanwhile/core.env /etc/meanwhile/voice.env
sudo chmod 600 /etc/meanwhile/key.env /etc/meanwhile/core.env /etc/meanwhile/voice.env
sudo mkdir -p /var/lib/meanwhile
sudo chown ubuntu:ubuntu /var/lib/meanwhile
sudo bash -c 'set -a; . /etc/meanwhile/key.env; /usr/local/bin/caddy validate --config /etc/caddy/Caddyfile >/dev/null 2>&1' \
  && echo "Caddyfile is valid" || echo "Caddyfile did NOT validate: run caddy validate by hand to see why"

step "systemd units"
for unit in caddy.service meanwhile-core.service meanwhile-voice.service meanwhile-nightly-stop.service meanwhile-nightly-stop.timer; do
  sudo install -m 644 "$HERE/$unit" "/etc/systemd/system/$unit"
done
sudo systemctl daemon-reload
sudo systemctl enable caddy meanwhile-core meanwhile-voice meanwhile-nightly-stop.timer

cat <<EOF

Done. Next, from your own terminal (values never pass through anyone else):
  1. sudo nano /etc/meanwhile/key.env      set MEANWHILE_KEY (16+ characters)
  2. sudo nano /etc/meanwhile/core.env     models, API keys; FCM file: scp it, then
     sudo install -m 600 -o ubuntu /tmp/fcm.json /etc/meanwhile/fcm.json && rm /tmp/fcm.json
  3. sudo systemctl restart caddy meanwhile-core meanwhile-voice
  4. Logs: journalctl -u meanwhile-core -f
  5. Pair phones and browsers: on a paired browser (or with the key in X-Meanwhile-Key) open
     https://$HOST/debug.html or https://alexa.$HOST/ and use "Pair a device", then scan its QR code.
     The first browser: bash ~/meanwhile/infra/cloud/pair-link.sh prints a one-time pairing link; open it there.
EOF
