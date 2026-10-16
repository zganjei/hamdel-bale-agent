"""Safe, tiny Cloudflare Workers AI connectivity check; never uses charity recordings."""
import base64
import io
import json
import math
import os
import struct
import sys
import urllib.request
import wave
from urllib.error import HTTPError, URLError

def beep_wav():
    out=io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000)
        # 2 seconds of tone; deliberately NOT a user's voice.
        pcm=bytearray()
        for i in range(32000):
            signal=int(2400*math.sin(2*math.pi*300*i/16000))
            pcm.extend(struct.pack("<h", signal))
        wav.writeframes(bytes(pcm))
    return out.getvalue()

def vendor_codes(body):
    try:
        obj=json.loads(body)
        return [x["code"] for x in obj.get("errors",[])
                if isinstance(x,dict) and type(x.get("code")) is int][:3]
    except (ValueError,TypeError,KeyError,UnicodeDecodeError):
        return []

def attempt(url, token, audio, minimal=False):
    data={"audio":audio}
    if not minimal:
        data.update(task="transcribe",language="fa",vad_filter=True,
                    condition_on_previous_text=False,beam_size=5,
                    initial_prompt="همدل، خیریه، کمک، کرون، تومان")
    req=urllib.request.Request(url, data=json.dumps(data).encode("utf-8"),
        headers={"Authorization":"Bearer "+token,"Content-Type":"application/json"},method="POST")
    try:
        with urllib.request.urlopen(req,timeout=90) as response:
            obj=json.load(response)
        if not isinstance(obj,dict):
            print("Cloudflare: HTTP 200 but unexpected response shape");return False,None
        if obj.get("success") is False:
            print("Cloudflare: HTTP 200 but success=false, codes",vendor_codes(json.dumps(obj)));return False,None
        result=obj.get("result")
        if isinstance(result,dict) and isinstance(result.get("text"),str):
            print("Cloudflare: authenticated inference OK (no user content sent)")
            return True,None
        print("Cloudflare: HTTP 200 but response schema does not contain result.text")
        return False,None
    except HTTPError as exc:
        codes=vendor_codes(exc.read(2048))
        print("Cloudflare: HTTP",exc.code,"error codes",codes)
        return False,exc.code
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        print("Cloudflare: connectivity failure",type(exc).__name__)
        return False,None

def main():
    account=os.environ.get("CLOUDFLARE_ACCOUNT_ID","").strip()
    token=os.environ.get("CLOUDFLARE_API_TOKEN","").strip()
    if not account or not token:
        print("Cloudflare speech smoke check skipped: credentials not configured")
        return 0
    if len(account)!=32 or any(c not in "0123456789abcdefABCDEF" for c in account):
        print("Cloudflare: invalid Account ID format (expect 32 hexadecimal characters)")
        return 1
    url=f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/@cf/openai/whisper-large-v3-turbo"
    audio=base64.b64encode(beep_wav()).decode("ascii")
    ok,status=attempt(url,token,audio)
    if not ok and status==400:
        print("Cloudflare: comparing minimal payload to isolate optional parameter rejection")
        ok,_=attempt(url,token,audio,minimal=True)
    return 0 if ok else 1

if __name__=="__main__":
    sys.exit(main())
