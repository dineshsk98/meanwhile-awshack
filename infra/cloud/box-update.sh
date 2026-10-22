#!/bin/bash
# Updates a set-up Meanwhile box after deploy.ps1 has unpacked new code: adds any settings missing from the files in
# /etc/meanwhile and rewrites the ones built from the public host (names printed, values never), creates the voice venv
# if it is missing, installs new packages, renders and validates the Caddyfile, installs the systemd units, restarts the
# services and prints their health.
# Run on the box as ubuntu: MEANWHILE_PUBLIC_HOST=<host> bash box-update.sh   (deploy.ps1 does this; safe to run again)
set -euo pipefail

if ! [[ "${MEANWHILE_PUBLIC_HOST:-}" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]]; then
  echo "MEANWHILE_PUBLIC_HOST must be set to the box's public host name (e.g. meanwhile.example.com)" >&2
  exit 1
fi
HOST="$MEANWHILE_PUBLIC_HOST"

HERE="$(cd "$(dirname "$0")" && pwd)"
CORE="$(cd "$HERE/../.." && pwd)"
VOICE="$CORE/voice"
UV="$HOME/.local/bin/uv"

# Prints a step heading.
step() { printf '\n== %s\n' "$1"; }

# Brings a live settings file in line with an example file: appends the settings it lacks and rewrites the ones whose
# example value holds __HOST__ (both with __HOST__ filled in from the optional third argument), printing only names.
add_missing() {
  sudo python3 - "$1" "$2" "${3:-__HOST__}" <<'EOF'
import re, sys
example, live, host = sys.argv[1], sys.argv[2], sys.argv[3]
secret = {"MEANWHILE_KEY", "MEANWHILE_CORE_KEY"}
setting = re.compile(r'^([A-Z0-9_]+)=')
lines = open(live).read().splitlines()
have = {m.group(1): i for i, m in ((i, setting.match(l)) for i, l in enumerate(lines)) if m}
add, updated = [], []
for line in open(example).read().splitlines():
    m = setting.match(line)
    if not m or m.group(1) in secret:
        continue
    name, want = m.group(1), line.replace("__HOST__", host)
    if name not in have:
        add.append(want)
    elif "__HOST__" in line and lines[have[name]] != want:
        lines[have[name]] = want
        updated.append(name)
if add:
    lines += [''] + add
if add or updated:
    with open(live, 'w') as f:
        f.write('\n'.join(lines) + '\n')
print(f"  {live}: added {[l.split('=', 1)[0] for l in add] or 'nothing'}, updated {updated or 'nothing'}")
EOF
}

step "Settings (names only)"
add_missing "$HERE/install.env.example" /etc/meanwhile/install.env
# shellcheck disable=SC1091
. /etc/meanwhile/install.env
add_missing "$HERE/core.env.example" /etc/meanwhile/core.env "$HOST"
add_missing "$HERE/voice.env.example" /etc/meanwhile/voice.env "$HOST"
sudo grep -qE '^MEANWHILE_OWNER_ID=.+' /etc/meanwhile/core.env || echo "  note: MEANWHILE_OWNER_ID is empty in core.env (set it to the owner's person id)"
CORE_PORT="$(sudo sed -n 's/^MEANWHILE_PORT=//p' /etc/meanwhile/core.env)"
if [ -z "$CORE_PORT" ]; then
  echo "  note: MEANWHILE_PORT is empty in core.env, so MEANWHILE_CORE_URL in voice.env was not checked"
elif sudo grep -qE "^MEANWHILE_CORE_URL=.*:$CORE_PORT/?\$" /etc/meanwhile/voice.env; then
  echo "  voice.env: MEANWHILE_CORE_URL matches the core's port"
else
  sudo sed -i -E "s#^(MEANWHILE_CORE_URL=.*:)[0-9]+/?\$#\1$CORE_PORT#" /etc/meanwhile/voice.env
  echo "  voice.env: MEANWHILE_CORE_URL updated to the core's MEANWHILE_PORT"
fi

step "Packages"
if [ ! -x "$VOICE/.venv/bin/python" ]; then
  "$UV" venv -q --allow-existing -p "$MEANWHILE_PYTHON" "$VOICE/.venv"
  echo "  created the voice venv in $VOICE/.venv"
fi
"$UV" pip install -q -p "$CORE/.venv/bin/python" -r "$CORE/requirements.txt"
"$UV" pip install -q -p "$VOICE/.venv/bin/python" -r "$VOICE/backend/requirements.txt"
echo "  both venvs match their requirements"

step "Caddyfile"
NEW="$(mktemp)"
sed "s/__HOST__/$HOST/g" "$HERE/Caddyfile.tmpl" > "$NEW"
if sudo bash -c "set -a; . /etc/meanwhile/key.env; /usr/local/bin/caddy validate --config '$NEW' --adapter caddyfile >/dev/null 2>&1"; then
  sudo install -m 644 "$NEW" /etc/caddy/Caddyfile
  echo "  valid; installed for $HOST"
else
  echo "  NOT valid; kept the running Caddyfile. See: sudo bash -c 'set -a; . /etc/meanwhile/key.env; caddy validate --config $NEW --adapter caddyfile'"
  exit 1
fi
rm -f "$NEW"

step "systemd units"
for unit in caddy.service meanwhile-core.service meanwhile-voice.service meanwhile-nightly-stop.service meanwhile-nightly-stop.timer; do
  sudo install -m 644 "$HERE/$unit" "/etc/systemd/system/$unit"
done
sudo systemctl daemon-reload

step "Restart"
SINCE="$(date '+%Y-%m-%d %H:%M:%S')"
sudo systemctl restart caddy meanwhile-core meanwhile-voice
for _ in $(seq 1 60); do
  journalctl -u meanwhile-core --since "$SINCE" --no-pager | grep -qE "Detector: .*(ready|failed)" && break
  sleep 1
done

step "Health"
for unit in caddy meanwhile-core meanwhile-voice ollama; do
  printf '  %-16s %s\n' "$unit" "$(systemctl is-active "$unit")"
done
journalctl -u meanwhile-core --since "$SINCE" --no-pager | sed 's/^.*python\[[0-9]*\]: //' \
  | grep -E "^(Push|Tracking|Detector|Faces|Model)|not set|is missing|Error|Traceback" | head -12 | sed 's/^/  core: /' || true
code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$(sudo sed -n 's/^MEANWHILE_VOICE_PORT=//p' /etc/meanwhile/voice.env)/voice/state")"
echo "  voice /voice/state: $code"
