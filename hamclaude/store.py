"""SQLite store. Raw audio, raw ASR text and every report are kept; nothing is deleted automatically."""
import json, sqlite3, time
from contextlib import closing
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS identities(username TEXT, sender_id INTEGER, PRIMARY KEY(username,sender_id));
CREATE TABLE IF NOT EXISTS items(
  chat INTEGER, msg_id INTEGER, arrived INTEGER, date INTEGER, origin TEXT, kind TEXT,
  file_id TEXT, filename TEXT, size INTEGER, duration INTEGER, text TEXT, path TEXT DEFAULT '',
  raw_asr TEXT DEFAULT '', prep TEXT DEFAULT '', note TEXT DEFAULT '', report_id INTEGER,
  PRIMARY KEY(chat,msg_id));
CREATE TABLE IF NOT EXISTS reports(
  id INTEGER PRIMARY KEY AUTOINCREMENT, created INTEGER, status TEXT DEFAULT 'open', attempts INTEGER DEFAULT 0,
  result TEXT DEFAULT '', parts TEXT DEFAULT '', delivered INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, path TEXT, caption TEXT DEFAULT '', created INTEGER);
"""

class Store:
    def __init__(self, root):
        self.root = Path(root); self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "media").mkdir(exist_ok=True)
        with closing(self._c()) as c: c.executescript(SCHEMA); c.commit()

    def _c(self):
        c = sqlite3.connect(self.root / "hamdel.sqlite3", timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        return c

    def q(self, sql, args=(), one=False, write=False):
        with closing(self._c()) as c:
            cur = c.execute(sql, args); rows = cur.fetchall() if not write else []
            if write: c.commit()
        return (rows[0] if rows else None) if one else rows

    def get(self, key, default=""):
        r = self.q("SELECT value FROM meta WHERE key=?", (key,), one=True); return r[0] if r else default
    def rules(self):
        try: r = json.loads(self.get("rules", "[]")); return [str(x) for x in r][:50]
        except ValueError: return []
    def set_rules(self, rules): self.put("rules", json.dumps([str(x)[:400] for x in rules][:50], ensure_ascii=False))
    def rules_text(self):
        """Standing instructions from the owner, shown to the agent every turn and to the report builder."""
        return "\n".join(f"{i}. {r}" for i, r in enumerate(self.rules(), 1))
    def put(self, key, value): self.q("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, str(value)), write=True)

    def know_sender(self, username, sid): self.q("INSERT OR IGNORE INTO identities VALUES(?,?)", (username, sid), write=True)
    def is_known_sender(self, username, sid):
        return bool(self.q("SELECT 1 FROM identities WHERE username=? AND sender_id=?", (username, sid), one=True))

    def add_item(self, it, now=None):
        self.q("""INSERT OR IGNORE INTO items(chat,msg_id,arrived,date,origin,kind,file_id,filename,size,duration,text)
                  VALUES(:chat,:msg_id,:arrived,:date,:origin,:kind,:file_id,:filename,:size,:duration,:text)""",
               {**it, "arrived": int(now or time.time())}, write=True)

    def close_ready(self, quiet, now=None):
        """Group items that stopped arriving into one report; returns the new report id or None."""
        now = int(now or time.time())
        last = self.q("SELECT max(arrived) m, count(*) n FROM items WHERE report_id IS NULL", one=True)
        if not last["n"] or now - last["m"] < quiet: return None
        with closing(self._c()) as c:
            rid = c.execute("INSERT INTO reports(created,status) VALUES(?,'pending')", (now,)).lastrowid
            c.execute("UPDATE items SET report_id=? WHERE report_id IS NULL", (rid,)); c.commit()
        return rid

    def queue_file(self, kind, path, caption=""):
        self.q("INSERT INTO outbox(kind,path,caption,created) VALUES(?,?,?,?)", (kind, str(path), caption[:3500], int(time.time())), write=True)
    def queue_text(self, text): self.queue_file("text", "", text[:3500])
    def take_outbox(self):
        rows = [dict(r) for r in self.q("SELECT * FROM outbox ORDER BY id")]
        for r in rows: self.q("DELETE FROM outbox WHERE id=?", (r["id"],), write=True)
        return rows

    def unprepared(self):
        """Oldest waiting media item whose file has not been downloaded/transcribed yet."""
        r = self.q("SELECT * FROM items WHERE report_id IS NULL AND kind!='text' AND file_id!='' AND path='' AND note='' ORDER BY date LIMIT 1", one=True)
        return dict(r) if r else None

    def pending_count(self): return self.q("SELECT count(*) n FROM items WHERE report_id IS NULL", one=True)["n"]

    def next_report(self):
        return self.q("SELECT * FROM reports WHERE status IN ('pending','delivering') ORDER BY id LIMIT 1", one=True)
    def items(self, rid): return [dict(r) for r in self.q("SELECT * FROM items WHERE report_id=? ORDER BY date,msg_id", (rid,))]
    def update_item(self, chat, msg_id, **kw):
        sets = ",".join(f"{k}=?" for k in kw)
        self.q(f"UPDATE items SET {sets} WHERE chat=? AND msg_id=?", (*kw.values(), chat, msg_id), write=True)
    def set_report(self, rid, **kw):
        sets = ",".join(f"{k}=?" for k in kw)
        self.q(f"UPDATE reports SET {sets} WHERE id=?", (*kw.values(), rid), write=True)
    def recent_reports(self, n=12):
        rows = self.q("SELECT id,created,result FROM reports WHERE result!='' ORDER BY id DESC LIMIT ?", (n,))
        out = []
        for r in reversed(rows):
            try: d = json.loads(r["result"])
            except ValueError: continue
            out.append({"id": r["id"], "date": time.strftime("%Y-%m-%d", time.gmtime(r["created"])),
                        "title": d.get("title", ""), "category": d.get("category", ""), "summary": d.get("summary", "")})
        return out
