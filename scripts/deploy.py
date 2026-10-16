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
for attempt in range(30):
    try:
        with urllib.request.urlopen('https://hamdel-reporter.liara.run/health',timeout=10) as r:
            health=json.load(r)
        if health.get('release')==expected and health.get('state')=='running':
            print('Verified running commit',expected)
            break
    except Exception:
        pass
    if attempt%5==0:print('Waiting for running health check',flush=True)
    time.sleep(2)
else:raise SystemExit('Deployment did not become healthy at the expected commit')
