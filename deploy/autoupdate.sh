#!/usr/bin/env bash
# Pulls the newest commit of the branch; installs it only if its tests pass and the service comes up healthy, else rolls back.
set -euo pipefail
R=zganjei/hamdel-bale-agent; B=hamdel-claude-bot; D=/opt/hamdel
GH=$(cat $D/gh-token)
sha=$(curl -fsS -H "Authorization: Bearer $GH" -H "Accept: application/vnd.github.sha" "https://api.github.com/repos/$R/commits/$B")
[ -n "$sha" ] || exit 1
[ "$sha" = "$(cat $D/version 2>/dev/null || true)" ] && exit 0
rm -rf $D/new; mkdir $D/new
curl -fsSL -H "Authorization: Bearer $GH" -L "https://api.github.com/repos/$R/tarball/$B" | tar xz -C $D/new --strip-components=1
(cd $D/new && python3 -m unittest discover -s tests >/dev/null 2>&1) || { echo "tests failed for $sha; keeping current version"; rm -rf $D/new; exit 1; }
printf '{"commit":"%s"}\n' "$sha" > $D/new/release.json
chown -R hamdel:hamdel $D/new
rm -rf $D/app.prev; mv $D/app $D/app.prev; mv $D/new $D/app
cp $D/app/deploy/autoupdate.sh $D/autoupdate.sh.next 2>/dev/null && mv $D/autoupdate.sh.next $D/autoupdate.sh || true   # keep the updater itself current
sed -i '/^GEMINI_MODEL=/d' $D/env
systemctl restart hamdel; sleep 10
if curl -fs localhost:8080/health >/dev/null; then echo "$sha" > $D/version; echo "updated to $sha"
else echo "unhealthy; rolling back"; rm -rf $D/app; mv $D/app.prev $D/app; systemctl restart hamdel; exit 1; fi
