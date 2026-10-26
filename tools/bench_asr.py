"""Compare speech-to-text services on YOUR voices.

Usage:  python tools/bench_asr.py voice1.ogg voice1.txt [voice2.ogg voice2.txt ...]
Each .txt holds the correct transcript you typed. Needs the same env vars as the bot; providers without keys are skipped.
Prints word error rate (lower is better) and seconds per provider. Audio is sent to the providers you have keys for.
"""
import os, re, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hamclaude import config
from hamclaude.media import Media

def norm(t):
    t = t.replace("ي", "ی").replace("ك", "ک").replace("‌", " ")
    return re.findall(r"[\w]+", re.sub(r"[؟?،,.!؛:]", " ", t).casefold())

def wer(ref, hyp):
    r, h = norm(ref), norm(hyp)
    d = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        prev, d[0] = d[0], i
        for j, hw in enumerate(h, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (rw != hw))
    return d[-1] / max(1, len(r))

def main(argv):
    cfg = config.load(); media = Media(cfg); pairs = list(zip(argv[::2], argv[1::2]))
    providers = [p for p, ok in (("gemini", cfg.gemini_key), ("cloudflare", cfg.cf_account and cfg.cf_token)) if ok]
    if not pairs or not providers: raise SystemExit(__doc__)
    for prov in providers:
        errs, secs = [], 0.0
        for audio, ref in pairs:
            with tempfile.TemporaryDirectory() as d:
                t0 = time.time()
                try: hyp = media.transcribe(Path(audio), d, provider=prov)
                except Exception as e: print(f"{prov}: {audio} FAILED ({type(e).__name__})"); continue
                secs += time.time() - t0
            e = wer(Path(ref).read_text(encoding="utf-8"), hyp); errs.append(e)
            print(f"{prov}: {Path(audio).name} WER={e:.0%}\n  {hyp[:300]}")
        if errs: print(f"== {prov}: average WER {sum(errs)/len(errs):.0%}, {secs:.0f}s total\n")

if __name__ == "__main__": main(sys.argv[1:])
