"""Bale-first ingestion, natural language to Codex, trusted approval + future outbox."""
from __future__ import annotations
import hashlib
import json
import os
import re
import sys
import time
import fcntl
from pathlib import Path
from .bale import Bale, BaleError
from .asr import PersianASR
from .codex_agent import AgentError, CodexAgent
from .store import Store


class HamdelService:
    def __init__(self, *, bale: Bale, store: Store, agent: CodexAgent,
                 allowed_users: set[int], asr=None):
        if not allowed_users:
            raise ValueError("BALE_ALLOWED_USER_IDS must contain the numeric ID of an administrator")
        self.bale, self.store, self.agent, self.asr = bale, store, agent, asr or PersianASR()
        self.allowed_users = allowed_users
        self.root = agent.workspace

    def snapshot(self):
        root = self.root / "context"
        root.mkdir(parents=True, exist_ok=True)
        (root / "summary.json").write_text(json.dumps(self.store.summary(), ensure_ascii=False, indent=2), encoding="utf-8")
        (root / "pending.json").write_text(json.dumps(self.store.list_drafts(), ensure_ascii=False, indent=2), encoding="utf-8")
        self.store.export_approved_csv(root / "approved_donations.csv")
        (root / "approved_content.json").write_text(json.dumps(
            self.store.approved_content(), ensure_ascii=False, indent=2), encoding="utf-8")

    def handle_update(self, update: dict):
        update_id = update.get("update_id")
        if not isinstance(update_id, int) or self.store.seen_update(update_id):
            return
        if "callback_query" in update:
            self.on_callback(update["callback_query"])
        elif "message" in update:
            self.on_message(update["message"])
        self.store.mark_update(update_id)

    def on_callback(self, callback: dict):
        user = (callback.get("from") or {}).get("id")
        cid = callback.get("id")
        if user not in self.allowed_users:
            if cid: self.bale.answer_callback(cid, "اجازهٔ تأیید ندارید.")
            return
        raw = str(callback.get("data", ""))
        chat = (callback.get("message") or {}).get("chat", {}).get("id")
        # No cross-chat approvals. Voice remains pending until explicit confirmation.
        vm = re.fullmatch(r"voice_(ok|no):([0-9a-f-]{36})", raw)
        if vm:
            action, voice_id = vm.groups()
            voice = self.store.get_voice(voice_id, chat)
            if not voice:
                if cid: self.bale.answer_callback(cid, "این ویس در این گفتگو پیدا نشد.")
                return
            if voice["status"] != "pending":
                if cid: self.bale.answer_callback(cid, "این ویس قبلاً بررسی شده است.")
                return
            if action == "no":
                self.store.decide_voice(voice_id, chat, status="rejected")
                if cid: self.bale.answer_callback(cid, "رد شد")
                self.bale.send(chat, "❌ این رونوشت رد شد. اگر خواستی متن درست را مستقیماً بنویس.")
                return
            # If Codex fails, the voice stays pending so the same callback can be retried.
            try:
                self._process_text(chat, voice["source"], voice["transcript"])
            except (AgentError, BaleError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
                if cid: self.bale.answer_callback(cid, "پردازش انجام نشد؛ دوباره تأیید کن.")
                self.bale.send(chat, "⚠️ " + str(exc)[:600])
                return
            self.store.decide_voice(voice_id, chat, status="processed")
            if cid: self.bale.answer_callback(cid, "متن تأیید شد و بررسی شد.")
            return
        match = re.fullmatch(r"(approve|reject):([0-9a-f-]{36})", raw)
        if not match:
            if cid: self.bale.answer_callback(cid, "دستور نامعتبر است.")
            return
        verb, draft_id = match.groups()
        draft = self.store.get_draft(draft_id)
        if chat is None or draft is None or not draft["source"].startswith(f"bale:{chat}:"):
            if cid: self.bale.answer_callback(cid, "این پیش‌نویس متعلق به این گفتگو نیست.")
            return
        status = self.store.decide(draft_id, approve=verb == "approve")
        if cid: self.bale.answer_callback(cid, "نتیجه: " + status)
        if chat:
            labels = {"approved": "✅ تأیید شد و در آرشیو همدل باقی می‌ماند (نه در تلگرام).",
                      "rejected": "❌ رد شد.", "duplicate_reference": "⛔ شماره پیگیری پرداخت تکراری است؛ چیزی ثبت نشد.",
                      "not_found": "این پیش‌نویس وجود ندارد."}
            self.bale.send(chat, labels.get(status, "این پیش‌نویس قبلاً " + status + " شده است."))
        self.snapshot()

    def _attachment(self, msg: dict, event: str) -> tuple[str, Path | None]:
        media, extension, category = None, None, None
        if msg.get("voice"):
            media, extension, category = msg["voice"], ".ogg", "voice"
        elif msg.get("audio"):
            media, extension, category = msg["audio"], ".m4a", "voice"
        elif msg.get("photo"):
            media, extension, category = msg["photo"][-1], ".jpg", "photo"
        elif msg.get("document"):
            media, category = msg["document"], "document"
            candidate = Path(media.get("file_name") or "file.bin").suffix.lower()
            extension = candidate if candidate in {".txt", ".csv", ".md", ".pdf", ".docx", ".jpg", ".jpeg", ".png", ".webp", ".ogg", ".mp3", ".m4a"} else ".bin"
        if media is None:
            return "", None
        target = self.root / "inbox" / (event + extension)
        if not target.is_file():
            self.bale.get_file(media["file_id"], target)
        if category == "voice" or extension in {".ogg", ".mp3", ".m4a"}:
            transcript = self.asr.transcribe(target)
            if not transcript:
                raise ValueError("از این ویس متنی قابل‌اعتماد استخراج نشد؛ لطفاً واضح‌تر ارسال کن.")
            return "[رونوشت صوت فارسی، برای نام‌ها و مبالغ حتماً تأیید بگیر]\n" + transcript, None
        if category == "photo" or extension in {".jpg", ".jpeg", ".png", ".webp"}:
            return "[تصویر ضمیمه شده است. محتوای آن را به‌عنوان دادهٔ تأییدنشده در نظر بگیر.]", target
        if extension in {".txt", ".csv", ".md"}:
            try:
                return "[محتوای فایل]\n" + target.read_text(encoding="utf-8")[:18000], None
            except UnicodeError:
                raise ValueError("فایل متنی باید UTF-8 باشد.")
        if extension == ".pdf":
            try:
                from pypdf import PdfReader
            except ImportError as exc:
                raise RuntimeError("برای فایل PDF کتابخانه pypdf را نصب کن.") from exc
            text = "\n".join(page.extract_text() or "" for page in PdfReader(str(target)).pages[:15])
            return "[محتوای PDF؛ در صورت اسکن بودن ممکن است متن وجود نداشته باشد]\n" + text[:18000], None
        if extension == ".docx":
            try:
                from docx import Document
            except ImportError as exc:
                raise RuntimeError("برای فایل DOCX کتابخانه python-docx را نصب کن.") from exc
            text = "\n".join(p.text for p in Document(str(target)).paragraphs)
            return "[محتوای فایل DOCX]\n" + text[:18000], None
        return "[فایل دریافت شد، ولی تبدیل این نوع فایل به متن فعلاً پشتیبانی نمی‌شود.]", None

    def _process_text(self, chat_id: int, source: str, text: str, image: Path | None = None):
        # A deterministic event ID gives retry-safe draft creation.
        event = hashlib.sha256(source.encode()).hexdigest()[:32]
        self.snapshot()
        answer, proposed = self.agent.ask(str(chat_id), text, event, image)
        if proposed is not None:
            # The trust boundary validates all fields again, independent of the LLM.
            draft_id, _ = self.store.create_draft(source, proposed)
            draft = self.store.get_draft(draft_id)
        else:
            draft_id, draft = None, None
        self.bale.send(chat_id, answer)
        if draft is not None:
            summary = f"📝 پیش‌نویس همدل\n\n{draft['title']}\n\n{draft['body'][:2800]}"
            if draft["kind"] == "donation":
                # Display the *actual* validated ledger fields, not merely LLM-authored prose.
                details = json.loads(draft["payload"])["data"]
                summary += (f"\n\n🔎 جزئیات دقیقِ قابل ثبت:" +
                            f"\nمبلغ: {details['amount']:,} {details['currency']}" +
                            f"\nتاریخ: {details['date']}" +
                            f"\nکد: {details.get('campaign_code') or 'بدون عنوان'}" +
                            f"\nشماره پیگیری: {details.get('payment_reference') or 'ندارد'}")
            summary += f"\n\nوضعیت: {draft['status']}"
            keyboard = None
            if draft["status"] == "pending":
                keyboard = {"inline_keyboard": [[
                    {"text": "✅ تأیید", "callback_data": "approve:" + draft_id},
                    {"text": "❌ رد", "callback_data": "reject:" + draft_id}
                ]]}
            self.bale.send(chat_id, summary, keyboard)

    def on_message(self, message: dict):
        chat = (message.get("chat") or {})
        sender = (message.get("from") or {})
        if sender.get("id") not in self.allowed_users or chat.get("type") != "private":
            return
        chat_id, mid = chat["id"], message.get("message_id")
        if not isinstance(mid, int):
            return
        source = f"bale:{chat_id}:{mid}"
        event = hashlib.sha256(source.encode()).hexdigest()[:32]
        try:
            body, image = self._attachment(message, event)
            text = "\n".join(p for p in [message.get("text") or message.get("caption") or "", body] if p).strip()
            if not text:
                return
            if body.startswith("[رونوشت صوت"):
                # Voice cannot reach the agent or financial drafts before a human checks it.
                voice_id = self.store.add_voice(source, chat_id, text)
                buttons = {"inline_keyboard": [[
                    {"text": "✅ متن درست است", "callback_data": "voice_ok:" + voice_id},
                    {"text": "❌ اشتباه است", "callback_data": "voice_no:" + voice_id}
                ]]}
                self.bale.send(chat_id, "🎧 متن شناسایی‌شده (هنوز اجرا نشده):\n\n" +
                               text[:3300] + "\n\nاول متن را بررسی کن. اگر اسم یا مبلغ اشتباه است، رد کن و متن صحیح را بفرست.", buttons)
                return
            self._process_text(chat_id, source, text, image)
        except (AgentError, BaleError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
            self.bale.send(chat_id, "⚠️ " + str(exc)[:700])

    def run(self):
        self.snapshot()
        lockpath = self.store.root / "bale-poll.lock"
        with lockpath.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Polling requires webhooks disabled. Do this once, not on every poll.
            self.bale.request("deleteWebhook")
            while True:
                try:
                    items = self.bale.get_updates(self.store.get_offset())
                    for update in items:
                        self.handle_update(update)
                        self.store.set_offset(update["update_id"] + 1)
                except KeyboardInterrupt:
                    return
                except Exception as exc:
                    print("Bale polling error:", type(exc).__name__, file=sys.stderr, flush=True)
                    time.sleep(3)


def main():
    token = os.environ.get("BALE_BOT_TOKEN", "").strip()
    ids = {int(i.strip()) for i in os.environ.get("BALE_ALLOWED_USER_IDS", "").split(",") if i.strip()}
    data = Path(os.environ.get("HAMDEL_DATA_DIR", "/data/hamdel"))
    workspace = data / "workspace"
    store = Store(data / "trusted")
    service = HamdelService(
        bale=Bale(token), store=store,
        agent=CodexAgent(workspace, store,
             model=os.environ.get("CODEX_MODEL", "gpt-6-luna"),
             effort=os.environ.get("CODEX_EFFORT", "low"),
             timeout=int(os.environ.get("CODEX_TIMEOUT", "300"))),
        allowed_users=ids,
        asr=PersianASR(model=os.environ.get("ASR_MODEL", "medium"),
                      device=os.environ.get("ASR_DEVICE", "cpu"),
                      compute_type=os.environ.get("ASR_COMPUTE_TYPE", "int8")),
    )
    service.run()

if __name__ == "__main__":
    main()
