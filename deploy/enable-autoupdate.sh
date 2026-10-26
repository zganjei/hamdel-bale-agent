#!/usr/bin/env bash
# One-time: sudo -E bash enable-autoupdate.sh   (needs GH exported). After this the server updates itself from GitHub every 10 minutes.
set -euo pipefail
[ -n "${GH:-}" ] || { echo "export GH first"; exit 1; }
umask 077; printf '%s' "$GH" > /opt/hamdel/gh-token; chmod 600 /opt/hamdel/gh-token
cat > /etc/systemd/system/hamdel-update.service <<'U'
[Unit]
Description=Update Hamdel bot from GitHub
[Service]
Type=oneshot
ExecStart=/bin/bash /opt/hamdel/app/deploy/autoupdate.sh
U
cat > /etc/systemd/system/hamdel-update.timer <<'U'
[Unit]
Description=Check GitHub for a new Hamdel bot version
[Timer]
OnBootSec=2min
OnUnitActiveSec=10min
[Install]
WantedBy=timers.target
U
systemctl daemon-reload; systemctl enable --now hamdel-update.timer
systemctl start hamdel-update.service || true
journalctl -u hamdel-update.service -n 5 --no-pager; echo ---; journalctl -u hamdel -n 15 --no-pager | cut -c1-200
