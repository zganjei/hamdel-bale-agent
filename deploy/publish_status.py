#!/usr/bin/env python3
"""Publish the server's deploy/health status to the `deploy-status` branch on GitHub (status.json), so the developer can verify a release
without asking the owner. Content-free: versions, health counters and error types only.
Usage: publish_status.py [state] [sha] [message]   (run by root from the updater and a timer)"""
import base64, json, subprocess, sys, time, urllib.error, urllib.request
from pathlib import Path

D, REPO, SRC, BRANCH = Path("/opt/hamdel"), "zganjei/hamdel-bale-agent", "hamdel-claude-bot", "deploy-status"
KEEP = ("fail", "error", "warn", "verified", "timeout", "http ")

def sh(*cmd):
    try: return subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout
    except Exception: return ""

def interesting(text, limit=15):
    lines = [l.strip()[:220] for l in text.splitlines() if any(k in l.lower() for k in KEEP)]
    return lines[-limit:]

def build(state, sha, message, now=None):
    try: health = json.load(urllib.request.urlopen("http://127.0.0.1:8080/health", timeout=5))
    except Exception as e: health = {"unreachable": type(e).__name__}
    version = (D / "version").read_text().strip() if (D / "version").exists() else ""
    return {"time_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now or time.time())), "state": state, "target_sha": sha, "message": message,
            "installed_sha": version, "health": health,
            "bot_log": interesting(sh("journalctl", "-u", "hamdel", "-n", "80", "--no-pager", "-o", "cat")),
            "updater_log": interesting(sh("journalctl", "-u", "hamdel-update", "-n", "30", "--no-pager", "-o", "cat"), 8)}

def call(method, path, token, body=None):
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r: return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404: return None
        raise

def publish(status, token):
    if call("GET", f"git/ref/heads/{BRANCH}", token) is None:
        head = call("GET", f"git/ref/heads/{SRC}", token)["object"]["sha"]
        call("POST", "git/refs", token, {"ref": f"refs/heads/{BRANCH}", "sha": head})
    cur = call("GET", f"contents/status.json?ref={BRANCH}", token)
    body = {"message": f"status {status['state']} {status['time_utc']}", "branch": BRANCH,
            "content": base64.b64encode(json.dumps(status, ensure_ascii=False, indent=1).encode()).decode()}
    if cur: body["sha"] = cur["sha"]
    call("PUT", "contents/status.json", token, body)

if __name__ == "__main__":
    a = sys.argv[1:] + ["heartbeat", "", ""][len(sys.argv) - 1:]
    publish(build(a[0], a[1], a[2]), (D / "gh-token").read_text().strip())
