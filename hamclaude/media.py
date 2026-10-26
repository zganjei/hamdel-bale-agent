"""ffmpeg helpers and Cloudflare Whisper ASR. Both are injectable so tests need no binaries or network."""
import base64, json, os, subprocess, time, urllib.error, urllib.request
from pathlib import Path
from . import keys

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

    def providers(self):
        """Order to try: the explicit choice, or (auto) ElevenLabs Scribe -> Gemini -> Cloudflare, whichever have keys."""
        cfg = self.cfg
        if cfg.asr_provider != "auto": return [cfg.asr_provider]
        order = [n for n, ok in (("elevenlabs", keys.get(cfg, "elevenlabs")), ("gemini", keys.get(cfg, "gemini")),
                                 ("cloudflare", cfg.cf_account and cfg.cf_token)) if ok]
        return order or ["cloudflare"]

    def provider(self): return self.providers()[0]

    def transcribe(self, path, folder, provider=None):
        """Raw Persian transcript of a voice/audio/video file ('' if there is no speech). Falls back to the next provider on failure."""
        last = None
        for prov in ([provider] if provider else self.providers()):
            try:
                return {"gemini": self._gemini, "elevenlabs": self._elevenlabs}.get(prov, self._cloudflare)(path, folder)
            except Exception as e: last = e
        raise last

    SCRIBE = ("scribe_v2", "scribe_v1")

    def _elevenlabs(self, path, folder):
        key = keys.get(self.cfg, "elevenlabs")
        if not key: raise RuntimeError("asr_not_configured")
        texts = []
        for seg in self.audio_segments(path, folder, seconds=1800):
            for model in self.SCRIBE:
                b = "hamdel_" + os.urandom(12).hex()
                fields = {"model_id": model, "language_code": "fas", "tag_audio_events": "false", "diarize": "false"}
                body = b"".join(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields.items())
                body += (f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{seg.name}"\r\nContent-Type: audio/mpeg\r\n\r\n').encode() \
                        + seg.read_bytes() + f"\r\n--{b}--\r\n".encode()
                req = urllib.request.Request("https://api.elevenlabs.io/v1/speech-to-text", data=body,
                                             headers={"xi-api-key": key, "Content-Type": f"multipart/form-data; boundary={b}"})
                try:
                    with self.open(req, timeout=120) as r: res = json.load(r)
                    texts.append(str(res.get("text", "")).strip()); break
                except urllib.error.HTTPError as e:
                    if e.code not in (400, 404, 422) or model == self.SCRIBE[-1]: raise     # unknown model id: try the older one
        return " ".join(t for t in texts if t).strip()

    MODELS_FALLBACK = ("gemini-flash-latest", "gemini-3.8-flash")
    SKIP = ("image", "tts", "live", "embed", "aqa", "imagen", "veo", "robotics", "computer", "audio", "customtools")

    def gemini_models(self):
        """Models this key can really use for generateContent (discovered from the API, newest 'flash' first)."""
        req = urllib.request.Request("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
                                     headers={"x-goog-api-key": keys.get(self.cfg, "gemini")})
        with self.open(req, timeout=20) as r: items = json.load(r).get("models", [])
        names = [m["name"].split("/")[-1] for m in items if "generateContent" in m.get("supportedGenerationMethods", [])]
        return sorted((n for n in names if "flash" in n and not any(k in n for k in self.SKIP)), reverse=True)

    def _gemini(self, path, folder, budget=240):
        cfg = self.cfg
        if not keys.get(cfg, "gemini"): raise RuntimeError("asr_not_configured")
        segments = self.audio_segments(path, folder, seconds=300)
        tried, last, end = set(), None, time.time() + budget
        queue = [getattr(self, "_gemini_ok", None) or cfg.gemini_model, *self.MODELS_FALLBACK]; discovered = False
        while time.time() < end:
            model = next((m for m in queue if m not in tried), None)
            if model is None:
                if discovered: break
                discovered = True
                try: queue += [m for m in self.gemini_models() if m not in queue]
                except Exception as e: last = last or e
                continue
            tried.add(model)
            try:
                text = self._gemini_with(model, segments); self._gemini_ok = model; return text
            except urllib.error.HTTPError as e:      # retired / overloaded / unavailable for this key: try the next model
                if e.code not in (400, 404, 429, 500, 502, 503, 504): raise
                last = e
            except (TimeoutError, urllib.error.URLError, OSError) as e:   # slow or unreachable: try the next model
                last = e
        raise last or RuntimeError("asr_failed")

    def _gemini_with(self, model, segments):
        cfg = self.cfg; texts = []
        for seg in segments:
            body = json.dumps({"contents": [{"parts": [{"text": GEMINI_PROMPT},
                {"inline_data": {"mime_type": "audio/mp3", "data": base64.b64encode(seg.read_bytes()).decode()}}]}],
                "generationConfig": {"temperature": 0}}).encode()
            req = urllib.request.Request(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", data=body,
                headers={"x-goog-api-key": keys.get(cfg, "gemini"), "Content-Type": "application/json"})
            with self.open(req, timeout=75) as r: res = json.load(r)
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
