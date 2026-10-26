#!/usr/bin/env bash
# One-time setup. GitHub pushes a webhook (POST /github, HMAC-signed) on every push to the branch and the server updates itself;
# a daily timer is only a safety net. Needs GH and WEBHOOK_SECRET exported.
set -euo pipefail
[ -n "${GH:-}" ] || { echo "export GH first"; exit 1; }
[ -n "${WEBHOOK_SECRET:-}" ] || { echo "export WEBHOOK_SECRET first"; exit 1; }
umask 077; printf '%s' "$GH" > /opt/hamdel/gh-token; chmod 600 /opt/hamdel/gh-token
curl -fsSL -H "Authorization: Bearer $GH" -H "Accept: application/vnd.github.raw" "https://api.github.com/repos/zganjei/hamdel-bale-agent/contents/deploy/autoupdate.sh?ref=hamdel-claude-bot" -o /opt/hamdel/autoupdate.sh
cat > /etc/systemd/system/hamdel-update.service <<'U'
[Unit]
Description=Update Hamdel bot from GitHub
[Service]
Type=oneshot
ExecStart=/bin/bash /opt/hamdel/autoupdate.sh
U
cat > /etc/systemd/system/hamdel-update.timer <<'U'
[Unit]
Description=Check GitHub for a new Hamdel bot version
[Timer]
OnBootSec=2min
OnUnitActiveSec=1d
[Install]
WantedBy=timers.target
U
grep -q '^GITHUB_WEBHOOK_SECRET=' /opt/hamdel/env && sed -i "s/^GITHUB_WEBHOOK_SECRET=.*/GITHUB_WEBHOOK_SECRET=$WEBHOOK_SECRET/" /opt/hamdel/env || echo "GITHUB_WEBHOOK_SECRET=$WEBHOOK_SECRET" >> /opt/hamdel/env
echo 'hamdel ALL=(root) NOPASSWD: /bin/systemctl start --no-block hamdel-update.service' > /etc/sudoers.d/hamdel-update; chmod 440 /etc/sudoers.d/hamdel-update; visudo -cf /etc/sudoers.d/hamdel-update
systemctl daemon-reload; systemctl enable --now hamdel-update.timer
systemctl start hamdel-update.service || true
journalctl -u hamdel-update.service -n 5 --no-pager; echo ---; journalctl -u hamdel -n 15 --no-pager | cut -c1-200
