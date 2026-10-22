#!/usr/bin/env bash
# Run on the box: prints a one-time pairing link (single use, MEANWHILE_PAIR_CODE_TTL_S seconds) for the first browser
# or phone. The key is read from /etc/meanwhile/key.env as root and passed to the core, never printed.
set -euo pipefail

PORT="$(sudo sed -n 's/^MEANWHILE_PORT=//p' /etc/meanwhile/core.env)"
if [ -z "$PORT" ]; then
  echo "MEANWHILE_PORT is not set in /etc/meanwhile/core.env" >&2
  exit 1
fi

sudo bash -c '. /etc/meanwhile/key.env; curl -fsS -X POST -H "X-Meanwhile-Key: $MEANWHILE_KEY" "http://127.0.0.1:$1/api/pairing-codes"' _ "$PORT" \
  | python3 -c 'import json, sys; print(json.load(sys.stdin)["pair_url"])'
