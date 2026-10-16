"""Bale HTTP adapter based on the official API. No SDK lock-in."""
from __future__ import annotations
import json
from pathlib import Path
import urllib.request
import urllib.error

class BaleError(RuntimeError):
    pass

class Bale:
    MAX_BYTES = 18 * 1024 * 1024
    def __init__(self, token: str):
        if not token:
            raise ValueError("BALE_BOT_TOKEN is required")
        self._api = f"https://tapi.bale.ai/bot{token}/"
        self._files = f"https://tapi.bale.ai/file/bot{token}/"

    def request(self, method: str, **kwargs):
        req = urllib.request.Request(self._api + method,
            data=json.dumps(kwargs, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=65) as response:
                data = json.load(response)
        except (urllib.error.URLError, TimeoutError) as exc:
            raise BaleError("خطای ارتباط با بله") from exc
        if not data.get("ok"):
            raise BaleError("بله درخواست را قبول نکرد: " + str(data.get("description", "unknown"))[:200])
        return data.get("result")

    def get_updates(self, offset: int):
        return self.request("getUpdates", offset=offset, timeout=25, limit=30, allowed_updates=["message", "callback_query"])

    def send(self, chat_id, text: str, reply_markup: dict | None = None):
        text = text.strip() or "(پاسخ خالی)"
        while text:
            piece, text = text[:3900], text[3900:]
            kw = {"chat_id": chat_id, "text": piece}
            if reply_markup and not text:
                kw["reply_markup"] = reply_markup
            self.request("sendMessage", **kw)

    def get_file(self, file_id: str, dest: Path):
        item = self.request("getFile", file_id=file_id)
        if item.get("file_size", 0) > self.MAX_BYTES:
            raise BaleError("حجم فایل برای دریافت بیش از حد مجاز است.")
        path = item.get("file_path", "")
        if not path or ".." in Path(path).parts or path.startswith("/") or ":" in path:
            raise BaleError("مسیر فایل نامعتبر است.")
        request = urllib.request.Request(self._files + path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(request, timeout=90) as res, dest.open("wb") as f:
            size = 0
            while chunk := res.read(64 * 1024):
                size += len(chunk)
                if size > self.MAX_BYTES:
                    dest.unlink(missing_ok=True)
                    raise BaleError("حجم فایل بیش از حد مجاز است.")
                f.write(chunk)
        return dest

    def answer_callback(self, callback_id: str, text: str = ""):
        return self.request("answerCallbackQuery", callback_query_id=callback_id, text=text)
