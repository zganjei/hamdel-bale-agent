"""Command-line tool the Claude Code agent uses to look at the archive and act. It never talks to Telegram directly and holds no secrets:
files to send are queued in the database and delivered to the owner by the bot process."""
import json, os, pty, re, select, shutil, subprocess, sys, time
from pathlib import Path
from .store import Store

KINDS = {"photo": "photo", "video": "video", "video_note": "video"}

def out(x): print(json.dumps(x, ensure_ascii=False, indent=1))

def item_row(r, full=False):
    d = {"id": r["rowid"], "kind": r["kind"], "time_utc": time.strftime("%Y-%m-%d %H:%M", time.gmtime(r["date"])),
         "origin": r["origin"], "report": r["report_id"], "has_file": bool(r["path"]), "text": r["text"] or "", "voice_transcript": r["raw_asr"] or ""}
    if r["note"]: d["note"] = r["note"]
    if full and r["path"]: d["file"] = r["path"]
    return d

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
LABEL = "تصویرسازی با هوش مصنوعی — عکس واقعی نیست"

def find_codex(env=None):
    home = Path((env or os.environ).get("HOME", "/home/hamdel"))
    path = os.pathsep.join([str(home / ".local/bin"), str(home / ".codex/bin"), "/usr/local/bin", "/usr/bin"])
    return shutil.which("codex", path=path)

def ensure_codex():
    if find_codex(): return find_codex()
    subprocess.run(["sh", "-c", "curl -fsSL https://chatgpt.com/codex/install.sh | sh"], capture_output=True, timeout=300)
    return find_codex()

def spawn_detached(args):
    subprocess.Popen([sys.executable, str(Path(__file__).resolve().parent.parent / "hamctl.py"), *args], start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=os.environ.copy())

def run_image(st, prompt, runner=subprocess.run):
    """Background job: make an image with Codex (ChatGPT subscription), then queue it for the owner."""
    codex = ensure_codex()
    if not codex: return st.queue_text("نصب Codex انجام نشد؛ تصویرسازی ممکن نیست.")
    gen = Path.home() / ".codex" / "generated_images"; start = time.time() - 2
    work = Path(os.environ.get("DATA_DIR", "data")).resolve() / "agent" / "img"; work.mkdir(parents=True, exist_ok=True)
    try: res = runner([codex, "exec", "--skip-git-repo-check", "$imagegen " + prompt[:1500]], cwd=str(work), capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired: return st.queue_text("ساخت تصویر بیش از حد طول کشید.")
    files = sorted((p for p in gen.rglob("*") if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp") and p.stat().st_mtime >= start), key=lambda p: p.stat().st_mtime) if gen.exists() else []
    if not files:
        tail = ((res.stdout or "") + (res.stderr or "")).strip()[-300:]
        return st.queue_text("تصویری ساخته نشد. اگر حساب ChatGPT وصل نیست، بگو «حساب ChatGPT را وصل کن».\n" + tail)
    out = Path(os.environ.get("DATA_DIR", "data")).resolve() / "generated"; out.mkdir(exist_ok=True)
    for i, f in enumerate(files[:4]):
        dest = out / f"{int(time.time())}_{i}{f.suffix.lower()}"; shutil.copyfile(f, dest); st.queue_file("photo", dest, LABEL)

def run_codex_login(st):
    """Background job: `codex login --device-auth`; sends the sign-in link and one-time code to the owner."""
    codex = ensure_codex()
    if not codex: return st.queue_text("نصب Codex انجام نشد.")
    master, slave = pty.openpty()
    p = subprocess.Popen([codex, "login", "--device-auth"], stdin=slave, stdout=slave, stderr=slave, close_fds=True, start_new_session=True)
    buf, sent, end = "", False, time.time() + 900
    while time.time() < end and p.poll() is None:
        if select.select([master], [], [], 1)[0]:
            try: buf += os.read(master, 4096).decode("utf-8", "ignore")
            except OSError: break
        clean = ANSI.sub("", buf)
        url, code = re.search(r"https://\S+", clean), re.search(r"\b[A-Z0-9]{4}-[A-Z0-9]{4,5}\b", clean)
        if not sent and url and code:
            st.queue_text(f"برای وصل کردن ChatGPT:\n۱) این لینک را باز کن و وارد حساب ChatGPT شو:\n{url.group(0)}\n۲) این کد را وارد کن: {code.group(0)}"); sent = True
    p.poll() is None and p.kill()
    ok = subprocess.run([codex, "login", "status"], capture_output=True, timeout=60).returncode == 0
    st.queue_text("حساب ChatGPT وصل شد. حالا می‌توانم تصویر بسازم." if ok else "وصل شدن ChatGPT کامل نشد؛ دوباره بگو تا از اول امتحان کنم.")

def main(argv, store=None, spawn=None):
    st = store or Store(os.environ.get("DATA_DIR", "data"))
    cmd, args = (argv or ["help"])[0], argv[1:]
    if cmd == "status":
        pend = st.q("SELECT rowid, * FROM items WHERE report_id IS NULL ORDER BY date")
        last = st.q("SELECT max(id) m FROM reports", one=True)["m"]
        out({"waiting_items": [item_row(r) for r in pend], "latest_report_id": last})
    elif cmd == "reports":
        out(st.recent_reports(int(args[0]) if args else 12))
    elif cmd == "report":
        r = st.q("SELECT * FROM reports WHERE id=?", (int(args[0]),), one=True)
        if not r: return out({"error": "no such report"})
        try: result = json.loads(r["result"])
        except ValueError: result = {}
        its = st.q("SELECT rowid, * FROM items WHERE report_id=? ORDER BY date", (r["id"],))
        out({"id": r["id"], "created_utc": time.strftime("%Y-%m-%d %H:%M", time.gmtime(r["created"])), "result": result, "items": [item_row(i) for i in its]})
    elif cmd == "item":
        r = st.q("SELECT rowid, * FROM items WHERE rowid=?", (int(args[0]),), one=True)
        out(item_row(r, full=True) if r else {"error": "no such item"})
    elif cmd == "search":
        q = f"%{' '.join(args)}%"
        rows = st.q("SELECT rowid, * FROM items WHERE text LIKE ? OR raw_asr LIKE ? OR note LIKE ? ORDER BY date DESC LIMIT 25", (q, q, q))
        reps = st.q("SELECT id FROM reports WHERE result LIKE ? ORDER BY id DESC LIMIT 25", (q,))
        out({"items": [item_row(r) for r in rows], "report_ids": [r["id"] for r in reps]})
    elif cmd == "send":
        r = st.q("SELECT rowid, * FROM items WHERE rowid=?", (int(args[0]),), one=True)
        if not r or not r["path"] or not Path(r["path"]).exists(): return out({"error": "no file for this item"})
        st.queue_file(KINDS.get(r["kind"], "document"), r["path"], " ".join(args[1:]) or f"آیتم {args[0]}")
        out({"queued": int(args[0])})
    elif cmd == "image":
        (spawn or spawn_detached)(["_image", " ".join(args)]); out({"started": "image is being made; it will arrive in the chat by itself in a few minutes"})
    elif cmd == "codex-login":
        (spawn or spawn_detached)(["_codex_login"]); out({"started": "the link and code are being sent to the owner"})
    elif cmd == "_image": run_image(st, " ".join(args))
    elif cmd == "_codex_login": run_codex_login(st)
    elif cmd == "report-now":
        n = st.pending_count()
        if n: st.put("flush", "1")
        out({"will_build_report_from_items": n})
    else:
        out({"commands": ["status", "reports [N]", "report ID", "item ID", "search TEXT", "send ITEM_ID [caption]", "report-now", "image DESCRIPTION", "codex-login"]})

if __name__ == "__main__": main(sys.argv[1:])
