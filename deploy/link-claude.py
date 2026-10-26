#!/usr/bin/env python3
"""Log the server's Claude Code in with your subscription, driven entirely from Telegram (no copying from the terminal).
Run once with sudo while the hamdel service is NOT running:  sudo python3 deploy/link-claude.py
1. You send /start to the bot.  2. It sends you the Claude login link.  3. You send back the code the page shows.
It then stores the long-lived token and your Telegram id in /opt/hamdel/env (never printed) and starts the service."""
import json, os, re, subprocess, sys, time, urllib.request

ENV = "/opt/hamdel/env"
SESSION = "cc"

def env_get(key):
    for line in open(ENV):
        if line.startswith(key + "="): return line.split("=", 1)[1].strip()
    return ""

def env_set(key, value):
    lines = [l for l in open(ENV).read().splitlines() if not l.startswith(key + "=")] + [f"{key}={value}"]
    open(ENV, "w").write("\n".join(lines) + "\n"); os.chmod(ENV, 0o600)
    subprocess.run(["chown", "root:root", ENV], check=False)

TOKEN = env_get("TELEGRAM_BOT_TOKEN")
def api(method, data=None, timeout=60):
    req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/{method}", data=json.dumps(data or {}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r: return json.load(r)["result"]

offset = 0
def next_message(chat=None, wait=900):
    global offset
    end = time.time() + wait
    while time.time() < end:
        for u in api("getUpdates", {"offset": offset, "timeout": 20, "allowed_updates": ["message"]}, 40):
            offset = u["update_id"] + 1
            m = u.get("message") or {}
            if m.get("chat", {}).get("type") == "private" and m.get("text") and (chat is None or m["chat"]["id"] == chat): return m
    sys.exit("timed out waiting for a Telegram message")

def pane():
    return subprocess.run(["tmux", "capture-pane", "-p", "-J", "-S", "-300", "-t", SESSION], capture_output=True, text=True).stdout

def wait_for(pattern, seconds):
    end = time.time() + seconds
    while time.time() < end:
        m = re.search(pattern, pane())
        if m: return m
        time.sleep(1)
    return None

def main():
    if os.geteuid() != 0: sys.exit("run with sudo")
    subprocess.run("apt-get install -y tmux >/dev/null 2>&1", shell=True)
    subprocess.run("systemctl stop hamdel 2>/dev/null; pkill -f 'claude setup-token'; tmux kill-session -t cc 2>/dev/null", shell=True)
    print("Now open Telegram and send /start to your bot. Waiting...")
    m = next_message(); chat = m["chat"]["id"]
    api("sendMessage", {"chat_id": chat, "text": "سلام. چند ثانیه صبر کن، لینک ورود Claude را می‌فرستم."})
    subprocess.run(["tmux", "new-session", "-d", "-x", "400", "-y", "50", "-s", SESSION, "sudo -iu hamdel claude setup-token"], check=True)
    link = wait_for(r"https://claude\.(?:com|ai)/\S+", 60)
    if not link: sys.exit("no login link appeared:\n" + pane()[-600:])
    api("sendMessage", {"chat_id": chat, "text": "۱) این لینک را باز کن، با حساب Claude وارد شو و Authorize را بزن.\n"
                        "۲) صفحه یک کد نشان می‌دهد. آن را کپی کن و همین‌جا برایم بفرست.\n\n" + link.group(0)})
    code = next_message(chat)["text"].strip()
    subprocess.run(["tmux", "send-keys", "-t", SESSION, "-l", code]); subprocess.run(["tmux", "send-keys", "-t", SESSION, "Enter"])
    tok = wait_for(r"(sk-ant-[A-Za-z0-9_\-]{20,})\s", 120)
    if not tok: sys.exit("no token appeared:\n" + pane()[-600:])
    env_set("CLAUDE_CODE_OAUTH_TOKEN", tok.group(1)); env_set("OWNER_ID", str(chat))
    subprocess.run(["tmux", "kill-session", "-t", SESSION])
    subprocess.run(["systemctl", "enable", "--now", "hamdel"], check=True)
    api("sendMessage", {"chat_id": chat, "text": "انجام شد. بات روشن است. یک ویس یا پیام فورواردشدهٔ همدل برایم بفرست؛ بعد از ۲ دقیقه گزارش را اینجا می‌گیری."})
    print("Done. Service started.")

main()
