"""Minimal Telegram Bot API client (stdlib only)."""
import json, os, urllib.request
from pathlib import Path

API = "https://api.telegram.org"

def chunks(text, size=3500):
    text = str(text or "").strip(); out = []
    while len(text) > size:
        cut = max(text.rfind("\n", 0, size), text.rfind(" ", 0, size))
        if cut < size // 2: cut = size
        out.append(text[:cut].strip()); text = text[cut:].strip()
    if text: out.append(text)
    return out

class Telegram:
    def __init__(self, token, opener=urllib.request.urlopen):
        self.token, self.open = token, opener

    def call(self, method, data=None, timeout=55):
        req = urllib.request.Request(f"{API}/bot{self.token}/{method}", data=json.dumps(data or {}).encode(),
                                     headers={"Content-Type": "application/json"})
        with self.open(req, timeout=timeout) as r:
            res = json.load(r)
        if not res.get("ok"): raise RuntimeError("Telegram rejected " + method)
        return res["result"]

    def updates(self, offset):
        return self.call("getUpdates", {"offset": offset, "limit": 50, "timeout": 25, "allowed_updates": ["message"]}, timeout=60)

    def send_message(self, chat, text):
        for part in chunks(text) or ["—"]:
            self.call("sendMessage", {"chat_id": chat, "text": part})

    def send_file(self, chat, kind, path, caption=""):
        field = {"photo": "photo", "video": "video", "document": "document"}[kind]
        method = {"photo": "sendPhoto", "video": "sendVideo", "document": "sendDocument"}[kind]
        content = Path(path).read_bytes(); b = "hamdel_" + os.urandom(12).hex()
        parts = [f'--{b}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{chat}\r\n'.encode()]
        if caption:
            parts.append(f'--{b}\r\nContent-Disposition: form-data; name="caption"\r\n\r\n{caption[:900]}\r\n'.encode())
        parts.append((f'--{b}\r\nContent-Disposition: form-data; name="{field}"; filename="{Path(path).name}"\r\n'
                      f'Content-Type: application/octet-stream\r\n\r\n').encode() + content + b"\r\n")
        parts.append(f"--{b}--\r\n".encode())
        req = urllib.request.Request(f"{API}/bot{self.token}/{method}", data=b"".join(parts),
                                     headers={"Content-Type": f"multipart/form-data; boundary={b}"})
        with self.open(req, timeout=120) as r:
            if not json.load(r).get("ok"): raise RuntimeError("Telegram rejected " + method)

    def download(self, file_id, dest, max_bytes):
        info = self.call("getFile", {"file_id": file_id})
        if (info.get("file_size") or 0) > max_bytes: raise OverflowError("too_large")
        remote = info["file_path"]
        if remote.startswith(("http:", "https:")) or ".." in remote.split("/"): raise RuntimeError("bad file path")
        dest = Path(dest); tmp = dest.with_suffix(dest.suffix + ".part"); n = 0
        try:
            with self.open(f"{API}/file/bot{self.token}/{remote}", timeout=90) as r, open(tmp, "wb") as out:
                while chunk := r.read(256 * 1024):
                    n += len(chunk)
                    if n > max_bytes: raise OverflowError("too_large")
                    out.write(chunk)
            if n == 0: raise RuntimeError("empty download")
            os.replace(tmp, dest)
        finally:
            tmp.unlink(missing_ok=True)
        return dest
