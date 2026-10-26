"""Command-line tool the Claude Code agent uses to look at the archive and act. It never talks to Telegram directly and holds no secrets:
files to send are queued in the database and delivered to the owner by the bot process."""
import json, os, sys, time
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

def main(argv, store=None):
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
    elif cmd == "report-now":
        n = st.pending_count()
        if n: st.put("flush", "1")
        out({"will_build_report_from_items": n})
    else:
        out({"commands": ["status", "reports [N]", "report ID", "item ID", "search TEXT", "send ITEM_ID [caption]", "report-now"]})

if __name__ == "__main__": main(sys.argv[1:])
