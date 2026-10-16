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
