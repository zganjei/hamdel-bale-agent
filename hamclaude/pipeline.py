"""Intake of Telegram messages, report preparation and delivery to the owner's private chat."""
import json, logging, time
from pathlib import Path

VOICE = ("voice", "audio", "video_note")
AUDIO_EXT = (".opus", ".ogg", ".mp3", ".m4a", ".wav")
MAX_CAPTION = 900

def item_from_message(m, origin):
    """Normalise a Telegram message into an item row, or None if it has nothing to report."""
    text = (m.get("text") or m.get("caption") or "").strip()
    kind, f = "text", {}
    doc = m.get("document") or {}
    if m.get("photo"): kind, f = "photo", max(m["photo"], key=lambda p: p.get("file_size", p.get("width", 0) * p.get("height", 0)))
    elif m.get("voice"): kind, f = "voice", m["voice"]
    elif m.get("audio"): kind, f = "audio", m["audio"]
    elif m.get("video_note"): kind, f = "video_note", m["video_note"]
    elif m.get("video"): kind, f = "video", m["video"]
    elif m.get("animation"): kind, f = "video", m["animation"]
    elif doc:
        name = str(doc.get("file_name") or "").lower(); mime = str(doc.get("mime_type") or "")
        kind = "audio" if name.endswith(AUDIO_EXT) or mime.startswith("audio/") else \
               "photo" if mime.startswith("image/") else "video" if mime.startswith("video/") else "document"
        f = doc
    if kind == "text" and not text: return None
    try: mid, chat = int(m["message_id"]), int(m["chat"]["id"])
    except (KeyError, TypeError, ValueError): return None
    return {"chat": chat, "msg_id": mid, "date": int(m.get("date") or time.time()), "origin": origin, "kind": kind,
            "file_id": f.get("file_id", ""), "filename": f.get("file_name") or "", "size": f.get("file_size") or 0,
            "duration": f.get("duration") or 0, "text": text}

class Pipeline:
    def __init__(self, cfg, store, tg, media, analyst, sleep=time.sleep):
        self.cfg, self.store, self.tg, self.media, self.analyst, self.sleep = cfg, store, tg, media, analyst, sleep

    # ---- preparation -------------------------------------------------
    def prepare(self, it):
        """Download and analyse one item's media. Failures are recorded on the item, never raised."""
        it["images"] = []
        if it["kind"] == "text" or not it["file_id"]: return it
        folder = self.store.root / "media"
        suffix = Path(it["filename"]).suffix[:8] or {"photo": ".jpg", "voice": ".ogg", "video": ".mp4"}.get(it["kind"], ".bin")
        try:
            path = Path(it["path"]) if it["path"] and Path(it["path"]).exists() else self.tg.download(
                it["file_id"], folder / f"{abs(it['chat'])}_{it['msg_id']}{suffix}", self.cfg.max_file_bytes)
            self.store.update_item(it["chat"], it["msg_id"], path=str(path)); it["path"] = str(path)
        except OverflowError:
            return self._note(it, "فایل بزرگ‌تر از ۲۰ مگابایت است و دریافت نشد.")
        except Exception:
            return self._note(it, "دریافت فایل ناموفق بود.")
        try:
            if it["kind"] == "photo":
                it["images"] = [self.media.image(path, folder / f"{path.stem}_n.jpg")]
            if it["kind"] in ("video", "video_note"):
                it["images"] = self.media.frames(path, folder)
            if it["kind"] in VOICE + ("video",) and not it["raw_asr"]:
                try:
                    it["raw_asr"] = self.media.transcribe(path, folder)
                    self.store.update_item(it["chat"], it["msg_id"], raw_asr=it["raw_asr"])
                    if not it["raw_asr"] and it["kind"] != "video": self._note(it, "گفتاری تشخیص داده نشد.")
                except Exception as e:
                    if it["kind"] != "video": self._note(it, "تبدیل صوت به متن انجام نشد؛ فایل صوتی برای شنیدن ضمیمه می‌شود.")
        except Exception:
            self._note(it, "پردازش تصویر یا صوت کامل نشد.")
        return it

    def _note(self, it, note):
        it["note"] = (it.get("note", "") + " " + note).strip()
        self.store.update_item(it["chat"], it["msg_id"], note=it["note"]); return it

    # ---- delivery plan -----------------------------------------------
    def build_parts(self, rid, items, result):
        by_id = {it["id"]: it for it in items}
        lines = [f"📋 گزارش #{rid} — {result['title']}", f"🗂 دسته: {result['category']}", "", result["summary"]]
        if result["key_points"]: lines += ["", "نکته‌ها:"] + [f"• {p}" for p in result["key_points"]]
        if result["amounts"]:
            lines += ["", "مبلغ‌های ذکرشده (تأییدنشده):"]
            lines += [f"• {a['what']}: {a['amount']} {a['currency']}" for a in result["amounts"]]
        if result["follow_ups"]: lines += ["", "پیگیری:"] + [f"• {p}" for p in result["follow_ups"]]
        if result["uncertain"]: lines += ["", "⚠️ نیازمند بررسی:"] + [f"• {p}" for p in result["uncertain"]]
        notes = [f"• آیتم {i['id']}: {i['note']}" for i in items if i.get("note")]
        if notes: lines += ["", "وضعیت فایل‌ها:"] + notes
        parts = [{"t": "msg", "text": "\n".join(lines)}]
        for t in result["transcripts"]:
            parts.append({"t": "msg", "text": f"🎧 متن کامل ویس (آیتم {t['item_id']}؛ ویرایش خودکار، متن خام در آرشیو است):\n\n{t['text']}"})
        wanted = list(result["relevant_item_ids"]) + [i["id"] for i in items if not i.get("seen") and i["kind"] in ("photo", "video", "video_note", "document")]
        sent = set()
        for iid in wanted:
            it = by_id.get(iid)
            if not it or iid in sent or not it.get("path") or it["kind"] in VOICE: continue
            sent.add(iid); kind = {"photo": "photo", "video": "video", "video_note": "video"}.get(it["kind"], "document")
            parts.append({"t": "file", "kind": kind, "path": it["path"], "caption": f"گزارش #{rid} — آیتم {iid}"})
        for it in items:  # keep the voice file itself so the owner can still listen to the original
            if it["kind"] in VOICE and it.get("path") and (it.get("note") or not it.get("raw_asr")):
                parts.append({"t": "file", "kind": "document", "path": it["path"], "caption": f"صوت اصلی آیتم {it['id']} (گزارش #{rid})"})
        return parts

    def fallback_parts(self, rid, items):
        text = [f"📋 گزارش #{rid} — تحلیل خودکار انجام نشد؛ محتوای خام ارسال می‌شود."]
        for it in items:
            body = " ".join(x for x in (it["text"], it.get("raw_asr", "")) if x)
            text.append(f"• آیتم {it['id']} ({it['kind']}): {body or '—'}" + (f" [{it['note']}]" if it.get("note") else ""))
        parts = [{"t": "msg", "text": "\n".join(text)}]
        parts += [{"t": "file", "kind": "photo" if it["kind"] == "photo" else "video" if it["kind"] in ("video", "video_note") else "document",
                   "path": it["path"], "caption": f"گزارش #{rid} — آیتم {it['id']}"} for it in items if it.get("path")]
        return parts

    # ---- run ---------------------------------------------------------
    def process_next(self):
        r = self.store.next_report()
        if not r: return False
        rid, owner = r["id"], int(self.cfg.owner_id)
        if r["status"] == "pending":
            rows = self.store.items(rid)
            items = []
            for row in rows:
                it = self.prepare(dict(row))
                it["id"] = row["msg_id"]; it["time"] = time.strftime("%H:%M", time.gmtime(row["date"])) + " UTC"
                items.append(it)
            try:
                result = self.analyst.analyze(items); parts = self.build_parts(rid, items, result)
                self.store.set_report(rid, result=json.dumps(result, ensure_ascii=False))
            except Exception as e:
                attempts = r["attempts"] + 1; self.store.set_report(rid, attempts=attempts)
                logging.warning("Analysis failed (%s), attempt %d; no content logged", type(e).__name__, attempts)
                if attempts < 3: self.sleep(10 * attempts); return True
                parts = self.fallback_parts(rid, items)
            self.store.set_report(rid, parts=json.dumps(parts, ensure_ascii=False), status="delivering")
            r = self.store.next_report()
        parts = json.loads(r["parts"]); done = r["delivered"]
        try:
            for i in range(done, len(parts)):
                p = parts[i]
                if p["t"] == "msg": self.tg.send_message(owner, p["text"])
                else: self.tg.send_file(owner, p["kind"], p["path"], p["caption"])
                self.store.set_report(rid, delivered=i + 1)
        except Exception as e:
            logging.warning("Delivery interrupted (%s); will resume", type(e).__name__); self.sleep(5); return True
        self.store.set_report(rid, status="done"); return True

    def answer_owner(self, question):
        try: reply = self.analyst.answer(question, self.store.recent_reports())
        except Exception: reply = "الان نتوانستم جواب بگیرم؛ کمی بعد دوباره بپرس."
        self.tg.send_message(int(self.cfg.owner_id), reply)
