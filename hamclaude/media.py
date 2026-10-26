"""ffmpeg helpers and Cloudflare Whisper ASR. Both are injectable so tests need no binaries or network."""
import base64, json, subprocess, urllib.request
from pathlib import Path

ASR_PROMPT = ("گفتار فارسی محاوره‌ای دربارهٔ کار خیریه: کمک، واریز، تومان، کرون، خانواده، نیازمند، "
              "بسته غذایی، توزیع، مراسم، هماهنگی.")

GEMINI_PROMPT = ("این فایل صوتی فارسی محاوره‌ای است (گفتار روزمره دربارهٔ کار خیریه). آن را کلمه‌به‌کلمه و دقیق رونویسی کن، "
                 "با همان لحن محاوره‌ای و بدون خلاصه‌کردن، اصلاح، ترجمه یا اضافه‌کردن توضیح. اسم‌ها و عددها را همان‌طور که گفته می‌شود بنویس. "
                 "اگر کلمه‌ای را مطمئن نیستی بهترین حدس را بنویس و بعد از آن «؟» بگذار. اگر گفتاری نیست، خروجی خالی بده. فقط متن رونویسی را برگردان.")

def _run(cmd, timeout=180):
    return subprocess.run(cmd, capture_output=True, timeout=timeout, check=True)

class Media:
    def __init__(self, cfg, opener=urllib.request.urlopen, run=_run):
        self.cfg, self.open, self.run = cfg, opener, run

    def duration(self, path):
        out = self.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]).stdout
        try: return float(out.decode().strip() or 0)
        except ValueError: return 0.0

    def image(self, path, out):
        """Normalise any image/frame to a ≤1280px JPEG that Claude can read."""
        self.run(["ffmpeg", "-y", "-i", str(path), "-vf", "scale='min(1280,iw)':-2", "-frames:v", "1", "-q:v", "3", str(out)])
        return out

    def frames(self, path, folder, n=4):
        d = self.duration(path) or 1.0; outs = []
        for i in range(n):
            out = Path(folder) / f"{Path(path).stem}_f{i}.jpg"
            self.run(["ffmpeg", "-y", "-ss", f"{d * (i + .5) / n:.2f}", "-i", str(path), "-frames:v", "1",
                      "-vf", "scale='min(1024,iw)':-2", "-q:v", "4", str(out)])
            if out.exists(): outs.append(out)
        return outs

    def audio_segments(self, path, folder, seconds=90):
        pattern = str(Path(folder) / (Path(path).stem + "_a%03d.mp3"))
        self.run(["ffmpeg", "-y", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k",
                  "-f", "segment", "-segment_time", str(seconds), pattern])
        return sorted(Path(folder).glob(Path(path).stem + "_a*.mp3"))

    def provider(self):
        cfg = self.cfg
        if cfg.asr_provider == "auto": return "gemini" if cfg.gemini_key else "cloudflare"
        return cfg.asr_provider

    def transcribe(self, path, folder, provider=None):
        """Raw Persian transcript of a voice/audio/video file ('' if there is no speech)."""
        provider = provider or self.provider()
        if provider == "gemini": return self._gemini(path, folder)
        return self._cloudflare(path, folder)

    def _gemini(self, path, folder):
        cfg = self.cfg
        if not cfg.gemini_key: raise RuntimeError("asr_not_configured")
        texts = []
        for seg in self.audio_segments(path, folder, seconds=300):
            body = json.dumps({"contents": [{"parts": [{"text": GEMINI_PROMPT},
                {"inline_data": {"mime_type": "audio/mp3", "data": base64.b64encode(seg.read_bytes()).decode()}}]}],
                "generationConfig": {"temperature": 0}}).encode()
            req = urllib.request.Request(
                f"https://generativelanguage.googleapis.com/v1beta/models/{cfg.gemini_model}:generateContent", data=body,
                headers={"x-goog-api-key": cfg.gemini_key, "Content-Type": "application/json"})
            with self.open(req, timeout=180) as r: res = json.load(r)
            parts = ((res.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
            texts.append("".join(p.get("text", "") for p in parts).strip())
        return " ".join(t for t in texts if t).strip()

    def _cloudflare(self, path, folder):
        cfg = self.cfg
        if not (cfg.cf_account and cfg.cf_token): raise RuntimeError("asr_not_configured")
        texts = []
        for seg in self.audio_segments(path, folder):
            body = json.dumps({"audio": base64.b64encode(seg.read_bytes()).decode(), "language": "fa",
                               "task": "transcribe", "initial_prompt": ASR_PROMPT}).encode()
            req = urllib.request.Request(
                f"https://api.cloudflare.com/client/v4/accounts/{cfg.cf_account}/ai/run/{cfg.asr_model}",
                data=body, headers={"Authorization": "Bearer " + cfg.cf_token, "Content-Type": "application/json"})
            with self.open(req, timeout=120) as r: res = json.load(r)
            if not res.get("success", True): raise RuntimeError("asr_failed")
            texts.append(str((res.get("result") or {}).get("text", "")).strip())
        return " ".join(t for t in texts if t).strip()
