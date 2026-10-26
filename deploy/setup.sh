#!/usr/bin/env bash
# Run once on a fresh Ubuntu server:  sudo bash deploy/setup.sh
set -euo pipefail
apt-get update -y && apt-get install -y python3 ffmpeg curl ca-certificates
id hamdel >/dev/null 2>&1 || useradd --system --create-home --home-dir /home/hamdel --shell /bin/bash hamdel
mkdir -p /opt/hamdel/data && cp -r . /opt/hamdel/app
chown -R hamdel:hamdel /opt/hamdel
# Claude Code (official installer), installed for the hamdel user
sudo -u hamdel bash -c 'curl -fsSL https://claude.ai/install.sh | bash'
install -m 644 deploy/hamdel.service /etc/systemd/system/hamdel.service
[ -f /opt/hamdel/env ] || { cp .env.example /opt/hamdel/env; chown hamdel:hamdel /opt/hamdel/env; chmod 600 /opt/hamdel/env; }
systemctl daemon-reload
echo "Done. Next: edit /opt/hamdel/env, then: sudo systemctl enable --now hamdel"
