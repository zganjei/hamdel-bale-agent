"""Free-tier analyst on Cloudflare Workers AI (same account/token as speech-to-text). No Anthropic API needed.

A vision model describes each photo/frame; a text model then writes the structured report. Both calls are
isolated: if vision fails, the media is still delivered, just marked as not analysed.
"""
import json, re, time, urllib.error, urllib.request
from pathlib import Path
from .analyst import SYSTEM, schema, Analyst

REQUIRED = {"title": "", "category": "", "summary": "", "key_points": [], "amounts": [], "follow_ups": [],
            "uncertain": [], "transcripts": [], "relevant_item_ids": []}

def extract_json(text):
    text = re.sub(r"<think>.*?</think>", "", str(text or ""), flags=re.S)
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            depth += (text[i] == "{") - (text[i] == "}")
            if depth == 0:
                try: return json.loads(text[start:i + 1])
                except ValueError: break
        start = text.find("{", start + 1)
    raise ValueError("no_json")

class CFAnalyst:
    def __init__(self, cfg, opener=urllib.request.urlopen, sleep=time.sleep):
        self.cfg, self.open, self.sleep = cfg, opener, sleep
        self.core = Analyst(cfg)          # reuse validation (_clean) and guards

    def _run(self, model, body):
        url = f"https://api.cloudflare.com/client/v4/accounts/{self.cfg.cf_account}/ai/run/{model}"
        for attempt in range(3):
            req = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode(), headers={
                "Authorization": "Bearer " + self.cfg.cf_token, "Content-Type": "application/json"})
            try:
                with self.open(req, timeout=120) as r: res = json.load(r)
                if res.get("success", True): return res.get("result") or {}
                raise RuntimeError("cf_failed")
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503) and attempt < 2: self.sleep(3 * (attempt + 1)); continue
                raise RuntimeError(f"cf_http_{e.code}") from None
            except (urllib.error.URLError, TimeoutError):
                if attempt < 2: self.sleep(3); continue
                raise RuntimeError("cf_unreachable") from None

    @staticmethod
    def _text(result):
        if isinstance(result.get("response"), str): return result["response"]
        ch = result.get("choices") or []
        return (ch[0].get("message") or {}).get("content") or "" if ch else ""

    def describe(self, path):
        body = {"prompt": "این تصویر را به فارسی در دو جمله توصیف کن: فقط چیزهایی که دیده می‌شود (اشیا، متن خوانا، تعداد). هویت آدم‌ها را حدس نزن.",
                "image": list(Path(path).read_bytes()), "max_tokens": 200}
        try: return self._text(self._run(self.cfg.vision_model, body)).strip()
        except RuntimeError:
            # Meta vision models need a one-time licence acceptance per account.
            self._run(self.cfg.vision_model, {"prompt": "agree"}); return self._text(self._run(self.cfg.vision_model, body)).strip()

    def analyze(self, items):
        budget = self.cfg.max_images
        for it in items:
            notes = []
            for img in it.get("images", []):
                if budget <= 0: break
                budget -= 1
                try: notes.append(self.describe(img)); it["seen"] = True
                except Exception: pass
            if notes: it["text"] = (it.get("text") or "") + "\nتوصیف خودکار تصویر: " + " | ".join(n for n in notes if n)
        lines = []
        for it in items:
            body = it.get("text") or ""
            if it.get("raw_asr"): body += ("\n" if body else "") + "متن خام تشخیص گفتار: " + it["raw_asr"]
            lines.append(f"[آیتم {it['id']} | {it['kind']} | {it['time']}]\n{body}")
        prompt = (SYSTEM + "\n\nخروجی فقط یک شیء JSON معتبر باشد (بدون توضیح اضافه) با این ساختار:\n" +
                  json.dumps(schema(self.cfg.categories), ensure_ascii=False) + "\n\nگزارش:\n" + "\n\n".join(lines))
        last = None
        for _ in range(2):
            try:
                out = extract_json(self._text(self._run(self.cfg.llm_model, {
                    "messages": [{"role": "user", "content": prompt}], "max_tokens": 3000, "temperature": 0.2})))
                return self.core._clean({**REQUIRED, **out}, items)
            except ValueError as e: last = e
        raise RuntimeError("cf_bad_json") from last

    def answer(self, question, reports):
        sys_ = "تو دستیار فارسی‌زبان خیریهٔ همدل هستی. کوتاه و طبیعی جواب بده و فقط از گزارش‌های داده‌شده استفاده کن؛ اگر در آن‌ها نیست بگو اطلاعات نداری."
        res = self._run(self.cfg.llm_model, {"messages": [
            {"role": "system", "content": sys_},
            {"role": "user", "content": f"گزارش‌های اخیر:\n{json.dumps(reports, ensure_ascii=False)}\n\nپرسش: {question[:4000]}"}], "max_tokens": 800})
        return re.sub(r"<think>.*?</think>", "", self._text(res), flags=re.S).strip() or "پاسخی دریافت نشد."
