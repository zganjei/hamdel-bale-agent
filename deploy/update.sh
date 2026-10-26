#!/usr/bin/env bash
# Update the installed bot to the latest code of the branch (keeps /opt/hamdel/env and data). Needs GH exported.
set -euo pipefail
[ -n "${GH:-}" ] || { echo "export GH first"; exit 1; }
curl -fsSL -H "Authorization: Bearer $GH" -L https://api.github.com/repos/zganjei/hamdel-bale-agent/tarball/hamdel-claude-bot | tar xz -C /opt/hamdel/app --strip-components=1
chown -R hamdel:hamdel /opt/hamdel/app
grep -q '^QUIET_SECONDS=' /opt/hamdel/env && sed -i 's/^QUIET_SECONDS=.*/QUIET_SECONDS=1800/' /opt/hamdel/env || echo 'QUIET_SECONDS=1800' >> /opt/hamdel/env
systemctl restart hamdel; sleep 3; curl -s localhost:8080/health; echo
