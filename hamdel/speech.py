"""Replaceable speech backend. Unusable audio never reaches the agent."""
import json
import os
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from .config import settings

class SpeechUnavailable(RuntimeError):
    pass

def transcribe(path, root, binary):
    cfg=settings()
    provider=cfg['speech_provider']
    if provider=='disabled':
        raise SpeechUnavailable('تبدیل صوت باکیفیت هنوز پیکربندی نشده؛ فایل محفوظ است. فعلاً متن را بنویس.')
    if provider=='cloudflare_free':
        return cloudflare_free_transcribe(path, cfg)
    if provider=='http':
        return remote_transcribe(path)
    if provider!='whisper_cpp':
        raise SpeechUnavailable('سرویس صوت شناخته نشد.')
    model=Path(root)/'speech'/cfg['speech_model_file']
    if not model.exists() or not Path(binary).exists():
        raise SpeechUnavailable('مدل یا ابزار تبدیل صوت آماده نیست؛ فایل محفوظ است.')
    probe=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration',
        '-of','default=noprint_wrappers=1:nokey=1',str(path)],capture_output=True,text=True,check=True,timeout=20)
    duration=float(probe.stdout.strip())
    if not 0<duration<=cfg['speech_max_seconds']:
        raise SpeechUnavailable(f"ویس باید حداکثر {cfg['speech_max_seconds']} ثانیه باشد؛ فایل محفوظ است.")
    result=[]
    with tempfile.TemporaryDirectory(prefix='speech-',dir=root) as folder:
        folder=Path(folder)
        chunk=cfg['speech_chunk_seconds']
        for index,start in enumerate(range(0,int(duration)+1,chunk)):
            if start>=duration:break
            wav=folder/f'{index}.wav';out=folder/f'{index}'
            subprocess.run(['ffmpeg','-nostdin','-v','error','-ss',str(start),'-i',str(path),
                '-t',str(chunk),'-ar','16000','-ac','1','-c:a','pcm_s16le',str(wav)],
                check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30)
            command=[str(binary),'-m',str(model),'-f',str(wav),'-l','fa','-t',str(cfg['speech_threads']),
                '-bs','3','-bo','3','-fa','-ac','1024','-sns','-otxt','-of',str(out),'-np','-nt']
            with tempfile.TemporaryFile() as log:
                child=subprocess.Popen(command,stdout=log,stderr=log)
                try:
                    began=time.monotonic()
                    while child.poll() is None:
                        memory=Path('/sys/fs/cgroup/memory.stat')
                        # Page cache is reclaimable; only guard anonymous allocations.
                        if memory.exists():
                            stat=dict(line.split() for line in memory.read_text().splitlines())
                            if int(stat.get('anon',0))>455*1024*1024:
                                raise SpeechUnavailable('حافظهٔ سرور برای مدل صوت کافی نیست؛ فایل محفوظ است.')
                        if time.monotonic()-began>cfg['speech_timeout']:
                            raise SpeechUnavailable('پردازش صوت طولانی شد؛ فایل محفوظ است.')
                        time.sleep(.25)
                    if child.returncode:
                        raise SpeechUnavailable('تبدیل صوت کامل نشد؛ فایل محفوظ است.')
                finally:
                    if child.poll() is None:child.kill();child.wait()
            result.append(out.with_suffix('.txt').read_text().strip())
    text=' '.join(result).strip()
    if not text or len(text)<3:
        raise SpeechUnavailable('متن قابل‌استفاده‌ای از این ویس استخراج نشد؛ فایل محفوظ است.')
    return text[:30000]

def remote_transcribe(path):
    """Optional private worker; URL/key are server secrets, never given to Codex."""
    url=os.environ.get('SPEECH_SERVICE_URL','')
    if not url.startswith('https://'):
        raise SpeechUnavailable('سرویس صوت بیرونی تنظیم نشده است.')
    data=Path(path).read_bytes()
    if len(data)>20*1024*1024:raise SpeechUnavailable('حجم صوت زیاد است.')
    headers={'Content-Type':'application/octet-stream','X-Speech-Language':'fa'}
    token=os.environ.get('SPEECH_SERVICE_TOKEN')
    if token:headers['Authorization']='Bearer '+token
    request=urllib.request.Request(url,data=data,headers=headers)
    with urllib.request.urlopen(request,timeout=180) as response:obj=json.load(response)
    text=obj.get('text','').strip()
    if not text:raise SpeechUnavailable('سرویس صوت متن معتبری برنگرداند.')
    return text[:30000]


def cloudflare_free_transcribe(path, cfg):
    """Use Workers AI's Free-plan allocation; no Whisper model runs on Liara.

    Only an explicitly configured Cloudflare account receives audio.
    Cloudflare Free refuses inference after quota exhaustion; don't auto-upgrade.
    """
    import base64
    import math
    from urllib.error import HTTPError, URLError

    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    if not token or len(account) != 32 or any(ch not in "0123456789abcdefABCDEF" for ch in account):
        raise SpeechUnavailable("سرویس رایگان صوت هنوز به حساب Cloudflare متصل نشده است؛ فایل محفوظ است.")
    path = Path(path)
    if not path.is_file() or path.stat().st_size > 20 * 1024 * 1024:
        raise SpeechUnavailable("فایل صوتی موجود نیست یا بیش از ۲۰ مگابایت است.")

    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, check=True, timeout=20)
        duration = float(probe.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise SpeechUnavailable("مدت صوت مشخص نشد؛ ابزار سبک ffprobe روی سرور لازم است.") from exc
    if not math.isfinite(duration) or not 0 < duration <= cfg.get("speech_max_seconds", 600):
        raise SpeechUnavailable("ویس طولانی یا نامعتبر است؛ حداکثر مدت ۱۰ دقیقه است.")

    url = ("https://api.cloudflare.com/client/v4/accounts/" + account +
           "/ai/run/@cf/openai/whisper-large-v3-turbo")
    outputs = []
    chunk_length = min(max(int(cfg.get("speech_chunk_seconds", 45)), 15), 60)
    # Encode each chunk to a small, valid audio file: never split compressed
    # OGG/Opus by arbitrary bytes, which would corrupt the speech.
    with tempfile.TemporaryDirectory(prefix="cloudflare-speech-") as folder:
        for idx, start in enumerate(range(0, math.ceil(duration), chunk_length)):
            if start >= duration:
                break
            mp3 = Path(folder) / (str(idx) + ".mp3")
            try:
                subprocess.run(
                    ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                     "-ss", str(start), "-i", str(path), "-t", str(min(chunk_length, duration-start)),
                     "-vn", "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "48k",
                     str(mp3)], check=True, capture_output=True, timeout=90)
            except (OSError, subprocess.SubprocessError) as exc:
                raise SpeechUnavailable("تبدیل اولیهٔ فایل صوتی کامل نشد؛ ffmpeg لازم است.") from exc
            if not mp3.is_file() or not 0 < mp3.stat().st_size <= 4 * 1024 * 1024:
                raise SpeechUnavailable("قطعهٔ صوتی آماده نشد؛ فایل اصلی محفوظ است.")
            payload = json.dumps({
                "audio": base64.b64encode(mp3.read_bytes()).decode("ascii"),
                "task": "transcribe", "language": "fa", "vad_filter": True,
                "condition_on_previous_text": False, "beam_size": 5,
                "initial_prompt": "همدل، خیریه، کمک، سوئیش، کرون، تومان، ریال، مبلغ، رسید"
            }).encode("utf-8")
            req = urllib.request.Request(
                url, data=payload, method="POST",
                headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=120) as response:
                    obj = json.load(response)
            except HTTPError as exc:
                # Never print Cloudflare's error body, which can reveal request details.
                if exc.code in (402, 403, 429):
                    raise SpeechUnavailable("سهمیه یا مجوز سرویس رایگان Cloudflare در دسترس نیست؛ هیچ هزینهٔ اضافه‌ای فعال نمی‌شود.") from None
                raise SpeechUnavailable("سرویس تبدیل صوت پاسخ معتبر نداد؛ دوباره امتحان کن.") from None
            except (OSError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
                raise SpeechUnavailable("اتصال امن به سرویس تبدیل صوت برقرار نشد؛ فایل محفوظ است.") from exc
            result = obj.get("result") if isinstance(obj, dict) else None
            text = result.get("text") if isinstance(result, dict) else None
            if obj.get("success") is False or not isinstance(text, str) or not text.strip():
                raise SpeechUnavailable("سرویس صوت رونوشت قابل استفاده نداد؛ حدس نمی‌زنم.")
            outputs.append(text.strip())
    transcript = " ".join(outputs).strip()
    if len(transcript) < 3:
        raise SpeechUnavailable("صدای قابل فهم تشخیص داده نشد؛ فایل محفوظ است.")
    return transcript[:30000]
