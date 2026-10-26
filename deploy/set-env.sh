#!/usr/bin/env bash
# sudo bash deploy/set-env.sh KEY   -> asks for the value and updates /opt/hamdel/env (default file; override with ENV_FILE)
set -euo pipefail
F=${ENV_FILE:-/opt/hamdel/env}; K=${1:?usage: set-env.sh KEY}
case "$K" in *[!A-Z0-9_]*|"") echo "bad key"; exit 1;; esac
read -rsp "$K: " V < /dev/tty; echo
case "$V" in *$'\n'*|*$'\r'*) echo "value must be one line"; exit 1;; esac
touch "$F"; grep -v "^${K}=" "$F" > "$F.tmp" || true
printf '%s=%s\n' "$K" "$V" >> "$F.tmp"; mv "$F.tmp" "$F"; chmod 600 "$F"
chown root:root "$F" 2>/dev/null || true   # root-only: the agent (user hamdel) must not read secrets
systemctl is-active --quiet hamdel && systemctl restart hamdel && echo "restarted" || echo "saved"
