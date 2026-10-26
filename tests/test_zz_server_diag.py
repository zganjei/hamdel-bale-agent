"""Temporary server-side diagnostic: when the updater runs the tests on the server and the suite fails, publish the failure output
to the deploy-status branch (the updater itself discards test output). Skipped everywhere else."""
import os, subprocess, sys, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKEN = Path("/opt/hamdel/gh-token")

class ServerDiag(unittest.TestCase):
    def test_publish_suite_failures_when_on_server(self):
        if os.environ.get("HAMDEL_DIAG_CHILD") or not TOKEN.exists(): self.skipTest("server only")
        r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=ROOT, env=dict(os.environ, HAMDEL_DIAG_CHILD="1"),
                           capture_output=True, text=True, timeout=600)
        if r.returncode:
            sys.path.insert(0, str(ROOT / "deploy")); import publish_status as ps
            ps.publish(ps.build("tests_failed", "", (r.stderr or "")[-2500:]), TOKEN.read_text().strip())
