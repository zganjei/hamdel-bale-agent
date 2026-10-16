import json
import re
import sys
from pathlib import Path

commit=sys.argv[1]
if not re.fullmatch(r'[0-9a-f]{40}',commit):raise SystemExit('Expected a full commit SHA')
Path('release.json').write_text(json.dumps({'commit':commit}))
