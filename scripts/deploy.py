"""The same deploy command is used by GitHub Actions and local maintenance."""
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

token=os.environ.get('LIARA_API_TOKEN','').strip()
if not token:raise SystemExit('LIARA_API_TOKEN is required')
expected=json.loads(Path('release.json').read_text())['commit']
subprocess.run(['liara','deploy','--app','hamdel-reporter','--api-token',token,
                '--no-app-logs','--message','GitHub '+expected[:12]],check=True)
for attempt in range(90):
    try:
        with urllib.request.urlopen('https://hamdel-reporter.liara.run/health',timeout=10) as r:
            health=json.load(r)
        from hamdel.config import settings
        speech_required = settings().get('speech_provider') == 'cloudflare_free'
        if (health.get('release') == expected and health.get('state') == 'running'
                and (not speech_required or health.get('speech_state') == 'ready')):
            print('Verified running commit',expected)
            break
    except Exception:
        pass
    if attempt % 5 == 0:
        safe_state = health if isinstance(locals().get('health'), dict) else {}
        print('Waiting for running health check:',
              'app=' + str(safe_state.get('state', 'unknown'))[:30],
              'release=' + str(safe_state.get('release', 'unknown'))[:12],
              'speech=' + str(safe_state.get('speech_state', 'unknown'))[:30],
              'issue=' + str(safe_state.get('speech_issue', 'unknown'))[:40],
              flush=True)
    time.sleep(2)
else:raise SystemExit('Deployment did not become healthy with working Cloudflare speech at the expected commit')
