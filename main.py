"""Hamdel report bot for Telegram. Entry point: python main.py (single polling process)."""
import json, logging, queue, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from hamclaude import config as cfgmod
from hamclaude.analyst import Analyst
from hamclaude.cf_analyst import CFAnalyst
from hamclaude.cc_analyst import ClaudeCodeAnalyst
from hamclaude.telegram import Telegram
from hamclaude.media import Media
from hamclaude.pipeline import Pipeline, item_from_message
from hamclaude.store import Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
STATE = {"state": "starting", "last_poll": 0, "updates": 0, "group_items": 0, "ignored_group": 0,
         "private_items": 0, "handler_errors": 0, "skipped_updates": 0, "last_error": "none"}

def obj(x): return x if isinstance(x, dict) else {}

class App:
    def __init__(self, cfg, store, tg, pipeline):
        self.cfg, self.store, self.tg, self.pipe = cfg, store, tg, pipeline
        self.questions = queue.Queue()

    def real_author(self, m):
        """The actual sender only. Forwarded-from and quoted users never qualify."""
        sc = obj(m.get("sender_chat")); who = sc or obj(m.get("from"))
        if not who or who.get("is_bot"): return False
        if not self.cfg.report_sender: return True      # no filter: any human in the configured group
        name = str(who.get("username") or "").strip().lstrip("@").casefold()
        try: sid = int(who.get("id"))
        except (TypeError, ValueError): sid = None
        if name == self.cfg.report_sender:
            if sid: self.store.know_sender(name, sid)
            return True
        return bool(sid and self.store.is_known_sender(self.cfg.report_sender, sid))

    def handle(self, u):
        m = u.get("message")
        if not isinstance(m, dict): return
        STATE["updates"] += 1
        chat, sender = obj(m.get("chat")), obj(m.get("from"))
        cid, uid = chat.get("id"), sender.get("id")
        text = (m.get("text") or "").strip()
        if text.split("@")[0].split(" ")[0] == "/whoami":
            self.tg.send_message(cid, f"chat_id: {cid}\nuser_id: {uid}\nusername: {sender.get('username') or '-'}")
            return
        if chat.get("type") in ("group", "supergroup"):
            if not self.cfg.group_chat_id or str(cid) != self.cfg.group_chat_id: return
            if not self.real_author(m): STATE["ignored_group"] += 1; return
            it = item_from_message(m, "group")
            if it: self.store.add_item(it); STATE["group_items"] += 1
            return
        if chat.get("type") == "private" and self.cfg.owner_id and str(uid) == self.cfg.owner_id:
            if text.split("@")[0].split(" ")[0] == "/report" or text in ("گزارش", "گزارش بده"):
                n = self.store.pending_count()
                if n: self.store.put("flush", "1"); self.tg.send_message(cid, f"{n} پیام جمع شده؛ گزارش را آماده می‌کنم. چند دقیقه صبر کن.")
                else: self.tg.send_message(cid, "پیام جدیدی برای گزارش نیست.")
                return
            forwarded = bool(m.get("forward_date") or m.get("forward_from") or m.get("forward_origin"))
            it = item_from_message(m, "private")
            if it and (it["kind"] != "text" or forwarded):
                self.store.add_item(it); STATE["private_items"] += 1
            elif it and not text.startswith("/"):
                self.questions.put(it["text"])
            elif text.split(" ")[0] in ("/start", "/help"):
                self.tg.send_message(cid, "سلام. گزارش‌های همدل (متن، ویس، عکس، فیلم) را از گروه می‌گیرم و خلاصه و دسته‌بندی‌شده با عکس و فیلم مرتبط همین‌جا می‌فرستم. "
                                       "ویس، عکس یا پیام فورواردشده را برایم بفرست؛ هر وقت /report بزنی گزارش را می‌سازم، یا اگر نیم ساعت چیز تازه‌ای نیاید خودم می‌سازم. هر متن ساده‌ای بنویسی، دربارهٔ گزارش‌های اخیر جواب می‌دهم.")

    def dispatch(self, u, attempts=3, pause=1):
        """A failing update is retried briefly, then skipped so later updates are never blocked."""
        for n in range(1, attempts + 1):
            try: self.handle(u); return True
            except Exception as e:
                STATE["handler_errors"] += 1; STATE["last_error"] = type(e).__name__[:60]
                logging.warning("Update failed (%s) %d/%d; no content logged", type(e).__name__, n, attempts)
                if n < attempts: time.sleep(pause)
        STATE["skipped_updates"] += 1; return False

    def poll(self):
        while True:
            try:
                me = self.tg.call("getMe"); logging.info("Telegram identity verified: %s", me.get("username")); break
            except Exception: STATE["state"] = "api_unreachable"; time.sleep(15)
        while True:
            try:
                for u in self.tg.updates(int(self.store.get("offset", "0"))):
                    self.dispatch(u); self.store.put("offset", u["update_id"] + 1)
                STATE.update(state="running", last_poll=int(time.time()))
            except Exception: STATE["state"] = "retrying"; time.sleep(10)

    def worker(self):
        while True:
            try:
                flush = self.store.get("flush") == "1"
                if flush: self.store.put("flush", "0")
                self.store.close_ready(0 if flush else self.cfg.quiet_seconds)
                if not self.pipe.process_next(): time.sleep(2)
            except Exception: logging.warning("Worker retrying; no content logged"); time.sleep(5)

    def chatter(self):
        while True:
            q = self.questions.get()
            try: self.pipe.answer_owner(q)
            except Exception: logging.warning("Question failed; no content logged")

def health(release):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in ("/", "/health"): self.send_error(404); return
            body = json.dumps({"service": "hamdel-claude-bot", "release": release, **STATE}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)
        def log_message(self, *a): pass
    return H

def build(cfg):
    store = Store(cfg.data_dir); tg = Telegram(cfg.bot_token)
    pipe = Pipeline(cfg, store, tg, Media(cfg), {"claude": Analyst, "cloudflare": CFAnalyst}.get(cfg.provider, ClaudeCodeAnalyst)(cfg))
    return App(cfg, store, tg, pipe)

if __name__ == "__main__":
    cfg = cfgmod.load()
    need = [("TELEGRAM_BOT_TOKEN", cfg.bot_token)] + ([("ANTHROPIC_API_KEY", cfg.anthropic_key)] if cfg.provider == "claude" else [])
    if not cfg.gemini_key:   # speech-to-text: Gemini key, or Cloudflare as fallback
        need += [("GEMINI_API_KEY (or CLOUDFLARE_*)", cfg.cf_account and cfg.cf_token)]
    missing = [k for k, v in need if not v]
    if missing: raise SystemExit("Missing environment variables: " + ", ".join(missing))
    app = build(cfg)
    rel = json.loads(Path("release.json").read_text()).get("commit", "local") if Path("release.json").exists() else "local"
    for target in (app.poll, app.worker, app.chatter): threading.Thread(target=target, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", cfg.port), health(rel)).serve_forever()
