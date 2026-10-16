"""Cloudflare free ASR: no paid API and no model on Liara."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from hamdel.speech import cloudflare_free_transcribe, SpeechUnavailable

ACCOUNT = "0123456789abcdef0123456789abcdef"
ENV = {"CLOUDFLARE_ACCOUNT_ID": ACCOUNT, "CLOUDFLARE_API_TOKEN": "nonproduction-test-key"}

class CloudflareTests(unittest.TestCase):
    def test_missing_credentials_do_not_send_audio(self):
        with patch.dict("os.environ", {"CLOUDFLARE_ACCOUNT_ID": "", "CLOUDFLARE_API_TOKEN": ""}):
            with patch("hamdel.speech.subprocess.run") as proc:
                with self.assertRaises(SpeechUnavailable):
                    cloudflare_free_transcribe("missing.opus", {"speech_max_seconds":600})
                proc.assert_not_called()

    def test_persian_transcription_uses_cloudflare_without_local_whisper(self):
        calls = []
        def fake_process(cmd, **kwargs):
            if cmd[0] == "ffprobe":
                return type("Result", (), {"stdout": "10.0"})()
            self.assertEqual(cmd[0], "ffmpeg")
            self.assertIn("libmp3lame", cmd)
            Path(cmd[-1]).write_bytes(b"mp3-data")
            return type("Result", (), {"stdout": ""})()
        def fake_urlopen(request, timeout):
            self.assertEqual(request.get_method(), "POST")
            self.assertIn(ACCOUNT, request.full_url)
            self.assertIn("whisper-large-v3-turbo", request.full_url)
            data = json.loads(request.data)
            self.assertEqual(data["language"], "fa")
            self.assertEqual(data["task"], "transcribe")
            self.assertTrue(data["condition_on_previous_text"])
            self.assertTrue(data["vad_filter"])
            calls.append(data)
            return io.BytesIO(json.dumps({"success": True, "result": {"text": "کمک به خانواده"}}).encode())
        with tempfile.TemporaryDirectory() as d, patch.dict("os.environ", ENV):
            src = Path(d)/"voice.opus"; src.write_bytes(b"opus")
            with patch("hamdel.speech.subprocess.run", side_effect=fake_process):
                with patch("hamdel.speech.urllib.request.urlopen", side_effect=fake_urlopen):
                    text = cloudflare_free_transcribe(src, {"speech_max_seconds":600,"speech_chunk_seconds":45})
        self.assertEqual(text,"کمک به خانواده")
        self.assertEqual(len(calls),1)


    def test_http_400_exposes_numeric_code_without_sensitive_body(self):
        import io
        from hamdel.speech import cloudflare_free_transcribe
        from urllib.error import HTTPError
        def fake_process(cmd, **kwargs):
            if cmd[0] == "ffprobe":return type("Result", (), {"stdout":"2"})()
            Path(cmd[-1]).write_bytes(b"mp3-data")
        response=io.BytesIO(json.dumps({"errors":[{"code":5006,"message":"private text"}]}).encode())
        with tempfile.TemporaryDirectory() as d, patch.dict("os.environ",ENV):
            path=Path(d)/"voice.ogg";path.write_bytes(b"ogg-data")
            with patch("hamdel.speech.subprocess.run",side_effect=fake_process):
                with patch("hamdel.speech.urllib.request.urlopen",
                           side_effect=HTTPError("https://cloudflare.invalid",400,"bad request",{},response)):
                    with self.assertRaises(SpeechUnavailable) as ctx:
                        cloudflare_free_transcribe(path,{"speech_max_seconds":600,"speech_chunk_seconds":45})
        self.assertIn("HTTP 400",str(ctx.exception))
        self.assertIn("5006",str(ctx.exception))
        self.assertNotIn("private text",str(ctx.exception))

    def test_pause_split(self):
        from hamdel.speech import speech_windows
        def fake(cmd,**kwargs):
            return type('Result',(),{'stderr':'silence_end: 21.2 | silence_duration: 0.4\nsilence_end: 46.1 | silence_duration: 0.6'})()
        with patch('hamdel.speech.subprocess.run',side_effect=fake):
            wins=speech_windows(Path('unused.opus'),71,26)
        self.assertEqual(wins[0],(0.0,21.0))
        self.assertEqual(wins[-1][1],71.0)
        self.assertLessEqual(max(y-x for x,y in wins),28)

    def test_join_removes_only_exact_overlap(self):
        from hamdel.speech import join_transcript_parts
        self.assertEqual(join_transcript_parts(['حسینیه را تمیز کردیم و',
             'تمیز کردیم و وسایل را بردیم']),
             'حسینیه را تمیز کردیم و وسایل را بردیم')

    def test_codex_proofreading_rejects_changed_numbers(self):
        from hamdel.speech import polish_transcript_with_codex
        raw='برای خانواده امروز ۱۵۰ کرون از حساب گرفتیم و کارهای خانه را انجام دادیم'
        with tempfile.TemporaryDirectory() as d:
            with patch('multimodal.codex',return_value={'text':'برای خانواده امروز ۲۰۰ کرون از حساب گرفتیم و کارهای خانه را انجام دادیم'}):
                self.assertEqual(polish_transcript_with_codex(raw,Path(d)),raw)

    def test_free_plan_quota_failure_is_safe(self):
        def fake_process(cmd, **kwargs):
            if cmd[0] == "ffprobe":return type("Result", (), {"stdout": "5"})()
            Path(cmd[-1]).write_bytes(b"test");return None
        with tempfile.TemporaryDirectory() as d, patch.dict("os.environ", ENV):
            src=Path(d)/"voice.ogg";src.write_bytes(b"opus")
            with patch("hamdel.speech.subprocess.run", side_effect=fake_process):
                with patch("hamdel.speech.urllib.request.urlopen",
                           side_effect=HTTPError("https://cloudflare.invalid",429,"quota",{},None)):
                    with self.assertRaisesRegex(SpeechUnavailable,"سهمیه"):
                        cloudflare_free_transcribe(src,{"speech_max_seconds":600,"speech_chunk_seconds":45})
