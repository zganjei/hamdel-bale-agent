#!/usr/bin/env bash
# Pulls the newest commit of the branch; installs it only if its tests pass and the service comes up healthy, else rolls back.
set -euo pipefail
R=zganjei/hamdel-bale-agent; B=hamdel-claude-bot; D=/opt/hamdel
GH=$(cat $D/gh-token)
notify() { ( set -a; . $D/env; curl -s -m 20 "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" --data-urlencode "chat_id=${OWNER_ID}" --data-urlencode "text=$1" ) >/dev/null 2>&1 || true; }
publish() { for f in $D/new/deploy/publish_status.py $D/app/deploy/publish_status.py; do [ -f $f ] && { python3 $f "$@" >/dev/null 2>&1 || true; return; }; done; }
# Heartbeat: publish status to GitHub every 30 minutes so the developer can verify the server without bothering the owner.
if [ ! -f /etc/systemd/system/hamdel-status.timer ]; then
  printf '[Unit]\nDescription=Publish Hamdel status\n[Service]\nType=oneshot\nExecStart=/usr/bin/python3 %s/app/deploy/publish_status.py heartbeat\n' "$D" > /etc/systemd/system/hamdel-status.service
  printf '[Unit]\nDescription=Publish Hamdel status\n[Timer]\nOnBootSec=3min\nOnUnitActiveSec=30min\n[Install]\nWantedBy=timers.target\n' > /etc/systemd/system/hamdel-status.timer
  systemctl daemon-reload; systemctl enable --now hamdel-status.timer
fi
# The bot (NoNewPrivileges, no sudo) requests an update by touching a file; this path unit starts the updater.
if [ ! -f /etc/systemd/system/hamdel-update.path ]; then
  printf '[Unit]\nDescription=Update request from the Hamdel bot\n[Path]\nPathModified=%s/update.trigger\nUnit=hamdel-update.service\n[Install]\nWantedBy=multi-user.target\n' "$D" > /etc/systemd/system/hamdel-update.path
  : > $D/update.trigger; chown hamdel:hamdel $D/update.trigger
  systemctl daemon-reload; systemctl enable --now hamdel-update.path
fi
sha=$(curl -fsS -H "Authorization: Bearer $GH" -H "Accept: application/vnd.github.sha" "https://api.github.com/repos/$R/commits/$B")
[ -n "$sha" ] || exit 1
[ "$sha" = "$(cat $D/version 2>/dev/null || true)" ] && exit 0
rm -rf $D/new; mkdir $D/new
curl -fsSL -H "Authorization: Bearer $GH" -L "https://api.github.com/repos/$R/tarball/$B" | tar xz -C $D/new --strip-components=1
(cd $D/new && python3 -m unittest discover -s tests >/dev/null 2>&1) || { echo "tests failed for $sha; keeping current version"; publish rejected "$sha" "tests failed on the server"; notify "⚠️ نسخهٔ جدید (${sha:0:7}) رد شد چون تست‌هایش روی سرور شکست خورد. نسخهٔ قبلی ادامه دارد."; rm -rf $D/new; exit 1; }
printf '{"commit":"%s"}\n' "$sha" > $D/new/release.json
chown -R hamdel:hamdel $D/new
rm -rf $D/app.prev; mv $D/app $D/app.prev; mv $D/new $D/app
cp $D/app/deploy/autoupdate.sh $D/autoupdate.sh.next 2>/dev/null && mv $D/autoupdate.sh.next $D/autoupdate.sh || true   # keep the updater itself current
sed -i '/^GEMINI_MODEL=/d' $D/env; chown root:root $D/env; chmod 600 $D/env   # secrets readable by root/systemd only
systemctl restart hamdel; sleep 10
if curl -fs localhost:8080/health >/dev/null; then echo "$sha" > $D/version; echo "updated to $sha"; msg=$(curl -fsS -H "Authorization: Bearer $GH" "https://api.github.com/repos/$R/commits/$sha" | python3 -c "import sys,json;print(json.load(sys.stdin)['commit']['message'].splitlines()[0])" 2>/dev/null || true); notify "✅ نسخهٔ جدید نصب شد (${sha:0:7}): $msg"; publish deployed "$sha" "$msg"
else echo "unhealthy; rolling back"; notify "⚠️ نسخهٔ جدید (${sha:0:7}) بالا نیامد؛ به نسخهٔ قبلی برگشتم."; rm -rf $D/app; mv $D/app.prev $D/app; systemctl restart hamdel; sleep 5; publish rolled_back "$sha" "new version did not become healthy"; exit 1; fi
