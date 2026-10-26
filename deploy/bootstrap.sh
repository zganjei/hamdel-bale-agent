#!/usr/bin/env bash
# One-paste installer for a fresh Ubuntu server (Oracle/Google Cloud).
#   export GH='<github token>'; curl -fsSL -H "Authorization: Bearer $GH" -H "Accept: application/vnd.github.raw" \
#     https://api.github.com/repos/zganjei/hamdel-bale-agent/contents/deploy/bootstrap.sh?ref=hamdel-claude-bot | sudo -E bash
# Secrets are typed/pasted at prompts and are written only to /opt/hamdel/env (mode 600).
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
[ -n "${GH:-}" ] || { echo "export GH first"; exit 1; }
ask() { local v; read -rsp "$1: " v < /dev/tty; echo >&2; printf '%s' "$v"; }
BALE=$(ask "Bale bot token")
code=$(curl -s -o /tmp/getme -w '%{http_code}' "https://tapi.bale.ai/bot${BALE}/getMe" || true)
grep -q '"ok":true' /tmp/getme 2>/dev/null && echo "Bale reachable from this server: OK" || { echo "Bale getMe FAILED (HTTP $code): $(head -c 200 /tmp/getme 2>/dev/null)"; echo "Wrong token, or Bale does not accept this server. Stopping."; exit 1; }
GEMINI=$(ask "Gemini API key (from aistudio.google.com; empty to use Cloudflare only)")
CFA=$(ask "Cloudflare account id (empty to skip)"); CFT=$(ask "Cloudflare API token (empty to skip)")
apt-get update -y && apt-get install -y python3 ffmpeg curl ca-certificates
if [ "$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo)" -lt 1800 ] && ! swapon --show | grep -q .; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile && echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
id hamdel >/dev/null 2>&1 || useradd --system --create-home --home-dir /home/hamdel --shell /bin/bash hamdel
mkdir -p /opt/hamdel/data /opt/hamdel/app
curl -fsSL -H "Authorization: Bearer $GH" -L https://api.github.com/repos/zganjei/hamdel-bale-agent/tarball/hamdel-claude-bot | tar xz -C /opt/hamdel/app --strip-components=1
cat > /opt/hamdel/env <<ENV
BALE_BOT_TOKEN=$BALE
LLM_PROVIDER=claudecode
CLAUDE_CODE_MODEL=sonnet
GEMINI_API_KEY=$GEMINI
GEMINI_MODEL=gemini-2.5-flash
CLOUDFLARE_ACCOUNT_ID=$CFA
CLOUDFLARE_API_TOKEN=$CFT
REPORT_SENDER=motahari58
QUIET_SECONDS=120
DATA_DIR=/opt/hamdel/data
ENV
chmod 600 /opt/hamdel/env; chown -R hamdel:hamdel /opt/hamdel
sudo -u hamdel bash -c 'curl -fsSL https://claude.ai/install.sh | bash'
install -m 644 /opt/hamdel/app/deploy/hamdel.service /etc/systemd/system/hamdel.service
systemctl daemon-reload
echo
echo "Installed. Remaining steps (secrets are asked at prompts, never shown):"
echo "  1) sudo -iu hamdel claude setup-token        # log in with your Claude subscription, copy the token it prints"
echo "  2) sudo bash /opt/hamdel/app/deploy/set-env.sh CLAUDE_CODE_OAUTH_TOKEN"
echo "  3) sudo bash /opt/hamdel/app/deploy/set-env.sh OWNER_ID ; sudo bash /opt/hamdel/app/deploy/set-env.sh GROUP_CHAT_ID"
echo "  4) sudo systemctl enable --now hamdel ; curl -s localhost:8080/health"
