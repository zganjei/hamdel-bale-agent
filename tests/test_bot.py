import base64, json, tempfile, time, unittest, urllib.error
from pathlib import Path
import main
from hamclaude.analyst import Analyst, guard_transcript
from hamclaude.telegram import chunks
from hamclaude.config import Config
from hamclaude.pipeline import Pipeline, item_from_message
from hamclaude.store import Store

JPEG = base64.b64decode("/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////wgALCAABAAEBAREA/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxA=")
GROUP, OWNER = -100, 42

class FakeTG:
    def __init__(self): self.sent, self.fail_after = [], None
    def call(self, m, d=None, timeout=0): return {"username": "x"}
    def send_message(self, chat, text):
        self._tick(); self.sent.append(("msg", chat, text))
    def send_file(self, chat, kind, path, caption=""):
        self._tick(); self.sent.append((kind, chat, Path(path).name, caption))
    def _tick(self):
        if self.fail_after is not None and len(self.sent) >= self.fail_after: self.fail_after = None; raise RuntimeError("down")
    def download(self, file_id, dest, max_bytes):
        if file_id == "big": raise OverflowError("too_large")
        Path(dest).write_bytes(JPEG); return Path(dest)

class FakeMedia:
    ASR = {"v1": "سلام ما امروز رفتیم بیست تا بسته غذایی دادیم به خانواده‌ها"}
    def image(self, path, out): Path(out).write_bytes(JPEG); return Path(out)
    def frames(self, path, folder, n=4): return []
    def transcribe(self, path, folder):
        for k, v in self.ASR.items():
            if k in Path(path).name or True: return v

class FakeAnalyst:
    def __init__(self, cfg): self.real = Analyst(cfg); self.calls = []; self.out = None
    def analyze(self, items):
        self.calls.append(items)
        for it in items:
            if it["images"]: it["seen"] = True
        out = self.out or {"title": "توزیع بسته", "category": "توزیع کمک غیرنقدی", "summary": "بیست بسته توزیع شد.",
               "key_points": ["بیست بسته"], "amounts": [], "follow_ups": [], "uncertain": [],
               "transcripts": [{"item_id": i["id"], "text": i["raw_asr"]} for i in items if i.get("raw_asr")],
               "relevant_item_ids": [i["id"] for i in items if i["kind"] == "photo"][:1]}
        return self.real._clean(json.loads(json.dumps(out)), items)
    def answer(self, q, reports): return "جواب: " + str(len(reports))

def voice(i, user="motahari58", uid=7, chat=GROUP, ctype="supergroup"):
    return {"update_id": i, "message": {"message_id": i, "date": int(time.time()), "chat": {"id": chat, "type": ctype},
            "from": {"id": uid, "username": user}, "voice": {"file_id": "v1", "duration": 5, "file_size": 100}}}
def photo(i, **kw):
    u = voice(i, **kw); m = u["message"]; del m["voice"]
    m["photo"] = [{"file_id": f"p{i}", "width": 10, "height": 10, "file_size": 50}]; m["caption"] = "عکس"; return u

class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.cfg = Config(data_dir=self.tmp.name, owner_id=str(OWNER), group_chat_id=str(GROUP), anthropic_key="k", quiet_seconds=60, report_sender="motahari58")
        self.store, self.tg, self.analyst = Store(self.tmp.name), FakeTG(), FakeAnalyst(self.cfg)
        self.pipe = Pipeline(self.cfg, self.store, self.tg, FakeMedia(), self.analyst, sleep=lambda s: None)
        self.app = main.App(self.cfg, self.store, self.tg, self.pipe)
        for k in ("updates", "group_items", "ignored_group", "private_items", "handler_errors", "skipped_updates"): main.STATE[k] = 0
    def run_reports(self, advance=1000):
        rid = self.store.close_ready(60, now=int(time.time()) + advance)
        while self.pipe.process_next(): pass
        return rid

class IntakeTests(Base):
    def test_only_real_sender_in_bound_group_is_collected(self):
        for u in (voice(1), voice(2, user="someone", uid=9), photo(3), voice(4, chat=-555)): self.app.dispatch(u)
        self.assertEqual(main.STATE["group_items"], 2); self.assertEqual(main.STATE["ignored_group"], 1)
    def test_forwarded_copy_by_other_sender_is_not_real_author(self):
        u = voice(5, user="someone", uid=9); u["message"]["forward_from"] = {"username": "motahari58", "id": 7}
        self.app.dispatch(u); self.assertEqual(main.STATE["group_items"], 0)
    def test_learned_id_still_counts_if_username_hidden(self):
        self.app.dispatch(voice(1)); u = voice(2, user=None); self.app.dispatch(u)
        self.assertEqual(main.STATE["group_items"], 2)
    def test_null_sender_and_chat_do_not_raise(self):
        self.app.dispatch({"update_id": 1, "message": {"message_id": 1, "chat": None, "from": None, "text": "x"}})
        u = voice(2); u["message"]["from"] = None; self.assertTrue(self.app.dispatch(u))
    def test_non_owner_private_is_ignored_owner_voice_collected(self):
        self.app.dispatch(voice(1, uid=99, chat=99, ctype="private")); self.assertEqual(main.STATE["private_items"], 0)
        self.app.dispatch(voice(2, uid=OWNER, chat=OWNER, ctype="private")); self.assertEqual(main.STATE["private_items"], 1)
    def test_owner_plain_text_is_a_question_not_a_report(self):
        u = {"update_id": 1, "message": {"message_id": 1, "chat": {"id": OWNER, "type": "private"}, "from": {"id": OWNER}, "text": "دیروز چی شد؟"}}
        self.app.dispatch(u); self.assertEqual(self.app.questions.get_nowait(), "دیروز چی شد؟"); self.assertEqual(main.STATE["private_items"], 0)
    def test_poison_update_is_skipped_not_blocking(self):
        calls = []
        def boom(u): calls.append(1); raise ValueError("x")
        self.app.handle = boom; self.app.dispatch({"update_id": 1}, pause=0)
        self.assertEqual((len(calls), main.STATE["skipped_updates"]), (3, 1))

class ReportTests(Base):
    def test_voice_and_photos_become_one_report_delivered_in_private(self):
        for u in (voice(1), photo(2), photo(3)): self.app.dispatch(u)
        self.assertIsNone(self.store.close_ready(60))        # still inside quiet window
        rid = self.run_reports()
        kinds = [s[0] for s in self.tg.sent]
        self.assertEqual(kinds[:2], ["msg", "msg"])           # summary, then full transcript
        self.assertIn("توزیع کمک غیرنقدی", self.tg.sent[0][2]); self.assertIn("بیست تا بسته", self.tg.sent[1][2])
        self.assertTrue(all(s[1] == OWNER for s in self.tg.sent))
        self.assertEqual(kinds.count("photo"), 1)             # only the photo judged relevant is sent
        self.assertEqual(self.store.q("SELECT status FROM reports WHERE id=?", (rid,), one=True)[0], "done")
        self.assertIn("بیست", self.store.items(rid)[0]["raw_asr"])   # raw ASR preserved
    def test_media_claude_never_saw_is_still_delivered(self):
        for u in (photo(1), photo(2)): self.app.dispatch(u)
        self.analyst.analyze = lambda items: self.analyst.real._clean({"title": "t", "category": "سایر", "summary": "s", "key_points": [],
            "amounts": [], "follow_ups": [], "uncertain": [], "transcripts": [], "relevant_item_ids": []}, items)
        self.run_reports(); self.assertEqual([s[0] for s in self.tg.sent].count("photo"), 2)
    def test_delivery_resumes_without_duplicates(self):
        for u in (voice(1), photo(2)): self.app.dispatch(u)
        self.tg.fail_after = 1; rid = self.run_reports()
        msgs = [s for s in self.tg.sent if s[0] == "msg"]
        self.assertEqual(len({m[2] for m in msgs}), len(msgs))
        self.assertEqual(self.store.q("SELECT status FROM reports WHERE id=?", (rid,), one=True)[0], "done")
    def test_analysis_failure_falls_back_to_raw_content(self):
        self.app.dispatch(voice(1)); self.app.dispatch(photo(2))
        def fail(items): raise RuntimeError("claude_http_500")
        self.analyst.analyze = fail; self.run_reports()
        self.assertIn("تحلیل خودکار انجام نشد", self.tg.sent[0][2]); self.assertIn("بیست تا بسته", self.tg.sent[0][2])
        self.assertIn("photo", [s[0] for s in self.tg.sent])
    def test_oversize_file_is_reported_not_silently_lost(self):
        u = photo(1); u["message"]["photo"][0]["file_id"] = "big"; self.app.dispatch(u); self.run_reports()
        self.assertIn("۲۰ مگابایت", self.tg.sent[0][2])
    def test_changed_number_in_cleaned_transcript_falls_back_to_raw(self):
        self.app.dispatch(voice(1))
        self.analyst.out = {"title": "t", "category": "سایر", "summary": "s", "key_points": [], "amounts": [], "follow_ups": [],
                            "uncertain": [], "relevant_item_ids": [], "transcripts": [{"item_id": 1, "text": "سلام ما امروز رفتیم سی تا بسته غذایی دادیم به خانواده‌ها"}]}
        self.run_reports(); text = self.tg.sent[1][2]
        self.assertIn("بیست تا بسته", text); self.assertIn("رد شد", self.tg.sent[0][2])
    def test_unknown_category_is_normalised(self):
        self.app.dispatch(photo(1)); self.analyst.out = {"title": "t", "category": "ناشناس", "summary": "s", "key_points": [], "amounts": [],
            "follow_ups": [], "uncertain": [], "transcripts": [], "relevant_item_ids": [99]}
        self.run_reports(); self.assertIn("سایر", self.tg.sent[0][2])

class UnitTests(unittest.TestCase):
    def test_chunks_lose_nothing(self):
        t = " ".join(f"w{i}" for i in range(3000)); parts = chunks(t, 1000)
        self.assertTrue(all(len(p) <= 1000 for p in parts)); self.assertEqual(" ".join(parts).split(), t.split())
    def test_guard_accepts_punctuation_only_change(self):
        self.assertTrue(guard_transcript("بیست تا بسته دادیم", "بیست تا بسته دادیم."))
        self.assertFalse(guard_transcript("بیست تا بسته", "سی تا بسته")); self.assertFalse(guard_transcript("20 تا", "۳۰ تا"))
    def test_item_from_message_kinds(self):
        base = {"message_id": 1, "chat": {"id": 1}, "date": 5}
        self.assertEqual(item_from_message({**base, "document": {"file_id": "a", "file_name": "n.opus"}}, "g")["kind"], "audio")
        self.assertEqual(item_from_message({**base, "document": {"file_id": "a", "mime_type": "image/png"}}, "g")["kind"], "photo")
        self.assertIsNone(item_from_message({**base}, "g"))
    def test_claude_request_shape_and_retry(self):
        cfg = Config(anthropic_key="KEY"); sent = []
        class R:
            def __init__(s, d): s.d = d
            def __enter__(s): return s
            def __exit__(s, *a): pass
            def read(s): return s.d
        calls = {"n": 0}
        def opener(req, timeout=0):
            calls["n"] += 1
            if calls["n"] == 1: raise urllib.error.HTTPError(req.full_url, 529, "busy", {}, None)
            sent.append(json.loads(req.data)); self.assertEqual(req.get_header("X-api-key"), "KEY")
            return R(json.dumps({"content": [{"type": "tool_use", "name": "submit_report", "input": {
                "title": "t", "category": "سایر", "summary": "s", "key_points": [], "amounts": [], "follow_ups": [],
                "uncertain": [], "transcripts": [], "relevant_item_ids": []}}]}).encode())
        with tempfile.TemporaryDirectory() as d:
            img = Path(d) / "a.jpg"; img.write_bytes(JPEG)
            out = Analyst(cfg, opener=opener, sleep=lambda s: None).analyze([{"id": 1, "kind": "photo", "time": "10:00", "text": "عکس", "images": [img]}])
        self.assertEqual(calls["n"], 2); self.assertEqual(out["category"], "سایر")
        body = sent[0]; self.assertEqual(body["tool_choice"]["name"], "submit_report")
        self.assertEqual(body["messages"][0]["content"][1]["type"], "image")


class CloudflareAnalystTests(unittest.TestCase):
    def test_free_tier_path_with_vision_and_think_tags(self):
        from hamclaude.cf_analyst import CFAnalyst, extract_json
        cfg = Config(cf_account="A", cf_token="T", data_dir=".")
        seen = []
        class R:
            def __init__(s, d): s.d = d
            def __enter__(s): return s
            def __exit__(s, *a): pass
            def read(s): return s.d
        report = {"title": "توزیع", "category": "توزیع کمک غیرنقدی", "summary": "ok", "key_points": [], "amounts": [],
                  "follow_ups": [], "uncertain": [], "transcripts": [{"item_id": 1, "text": "سلام ما بیست تا بسته دادیم"}], "relevant_item_ids": [2]}
        def opener(req, timeout=0):
            body = json.loads(req.data); seen.append((req.full_url.split("/run/")[1], body))
            if "image" in body: return R(json.dumps({"success": True, "result": {"response": "چند بسته روی میز"}}).encode())
            return R(json.dumps({"success": True, "result": {"choices": [{"message": {"content": "<think>x{</think>```json\n" + json.dumps(report, ensure_ascii=False) + "\n```"}}]}}).encode())
        with tempfile.TemporaryDirectory() as d:
            img = Path(d) / "a.jpg"; img.write_bytes(JPEG)
            items = [{"id": 1, "kind": "voice", "time": "10:00", "text": "", "raw_asr": "سلام ما بیست تا بسته دادیم", "images": []},
                     {"id": 2, "kind": "photo", "time": "10:01", "text": "", "images": [img]}]
            out = CFAnalyst(cfg, opener=opener, sleep=lambda s: None).analyze(items)
        self.assertEqual(out["relevant_item_ids"], [2]); self.assertTrue(items[1]["seen"])
        self.assertIn("چند بسته روی میز", seen[-1][1]["messages"][0]["content"])
        self.assertTrue(seen[-1][0].startswith("@cf/qwen")); self.assertEqual(seen[0][1]["image"][:2], list(JPEG[:2]))
        self.assertEqual(extract_json('x {"a": {"b": 1}} y'), {"a": {"b": 1}})

    def test_vision_failure_leaves_media_unseen_so_it_is_still_sent(self):
        from hamclaude.cf_analyst import CFAnalyst
        cfg = Config(cf_account="A", cf_token="T"); a = CFAnalyst(cfg, sleep=lambda s: None)
        a.describe = lambda p: (_ for _ in ()).throw(RuntimeError("x"))
        a._run = lambda model, body: {"response": json.dumps({"title": "t", "category": "سایر", "summary": "s"})}
        with tempfile.TemporaryDirectory() as d:
            img = Path(d) / "a.jpg"; img.write_bytes(JPEG); items = [{"id": 2, "kind": "photo", "time": "x", "text": "", "images": [img]}]
            out = a.analyze(items)
        self.assertFalse(items[0].get("seen")); self.assertEqual(out["category"], "سایر")


class ClaudeCodeAnalystTests(unittest.TestCase):
    def test_command_is_locked_down_and_structured_output_is_used(self):
        from hamclaude.cc_analyst import ClaudeCodeAnalyst
        with tempfile.TemporaryDirectory() as d:
            cfg = Config(data_dir=d); img = Path(d) / "a.jpg"; img.write_bytes(JPEG); seen = {}
            report = {"title": "t", "category": "توزیع کمک غیرنقدی", "summary": "s", "key_points": [], "amounts": [], "follow_ups": [],
                      "uncertain": [], "transcripts": [{"item_id": 1, "text": "سلام ما بیست تا بسته دادیم"}], "relevant_item_ids": [2, 77]}
            def run(cmd, cwd=None, env=None, **kw):
                seen.update(cmd=cmd, files=sorted(p.name for p in Path(cwd).iterdir()), env=env, text=(Path(cwd) / "report.txt").read_text())
                return type("R", (), {"returncode": 0, "stdout": json.dumps({"is_error": False, "structured_output": report})})()
            items = [{"id": 1, "kind": "voice", "time": "10:00", "text": "", "raw_asr": "سلام ما بیست تا بسته دادیم", "images": []},
                     {"id": 2, "kind": "photo", "time": "10:01", "text": "عکس", "images": [img]}]
            out = ClaudeCodeAnalyst(cfg, run=run).analyze(items)
        c = seen["cmd"]
        self.assertEqual(c[c.index("--tools") + 1], "Read"); self.assertEqual(c[c.index("--permission-mode") + 1], "dontAsk")
        self.assertNotIn("--dangerously-skip-permissions", c); self.assertIn("--json-schema", c)
        self.assertEqual(seen["files"], ["item2_0.jpg", "report.txt"]); self.assertIn("بیست تا بسته", seen["text"])
        self.assertEqual(out["relevant_item_ids"], [2]); self.assertTrue(items[1]["seen"])
        self.assertFalse(any(k.startswith(("TELEGRAM", "CLOUDFLARE", "ANTHROPIC")) for k in seen["env"]))

    def test_failures_become_runtime_errors_so_fallback_delivery_works(self):
        from hamclaude.cc_analyst import ClaudeCodeAnalyst
        with tempfile.TemporaryDirectory() as d:
            for run in (lambda *a, **k: type("R", (), {"returncode": 1, "stdout": "{}"})(),
                        lambda *a, **k: type("R", (), {"returncode": 0, "stdout": "not json"})(),
                        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError())):
                with self.assertRaises(RuntimeError): ClaudeCodeAnalyst(Config(data_dir=d), run=run).analyze([{"id": 1, "kind": "text", "time": "x", "text": "a", "images": []}])


class AsrProviderTests(unittest.TestCase):
    def test_gemini_request_shape_and_auto_selection(self):
        import subprocess
        from hamclaude.media import Media
        cfg = Config(gemini_key="G", gemini_model="gemini-2.5-flash"); seen = {}
        class R:
            def __init__(s, d): s.d = d
            def __enter__(s): return s
            def __exit__(s, *a): pass
            def read(s): return s.d
        def opener(req, timeout=0):
            seen["url"], seen["hdr"], seen["body"] = req.full_url, dict(req.header_items()), json.loads(req.data)
            return R(json.dumps({"candidates": [{"content": {"parts": [{"text": "سلام ما بیست تا بسته دادیم"}]}}]}).encode())
        with tempfile.TemporaryDirectory() as d:
            seg = Path(d) / "a_a000.mp3"; seg.write_bytes(b"abc")
            m = Media(cfg, opener=opener, run=lambda *a, **k: None); m.audio_segments = lambda p, f, seconds=90: [seg]
            self.assertEqual(m.provider(), "gemini")
            self.assertEqual(m.transcribe(Path(d) / "a.ogg", d), "سلام ما بیست تا بسته دادیم")
        self.assertIn("gemini-2.5-flash:generateContent", seen["url"]); self.assertEqual(seen["hdr"]["X-goog-api-key"], "G")
        part = seen["body"]["contents"][0]["parts"][1]["inline_data"]; self.assertEqual(part["mime_type"], "audio/mp3")
        self.assertEqual(Media(Config(cf_account="a", cf_token="t")).provider(), "cloudflare")

    def test_wer(self):
        import sys; sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
        from bench_asr import wer
        self.assertEqual(wer("بیست تا بسته دادیم", "بیست تا بسته دادیم."), 0)
        self.assertAlmostEqual(wer("بیست تا بسته دادیم", "سی تا بسته دادیم"), .25)




class OpenGroupTests(unittest.TestCase):
    def test_empty_sender_filter_accepts_humans_not_bots(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Config(data_dir=d, owner_id=str(OWNER), group_chat_id=str(GROUP), anthropic_key="k")
            app = main.App(cfg, Store(d), FakeTG(), None)
            self.assertTrue(app.real_author({"from": {"id": 1, "username": "anyone"}}))
            self.assertFalse(app.real_author({"from": {"id": 2, "is_bot": True}}))


class ReportCommandTests(IntakeTests if 'IntakeTests' in globals() else unittest.TestCase):
    def test_report_command_flushes_and_empty_answer_is_fixed(self):
        self.app.handle({"update_id": 1, "message": {"message_id": 900, "date": int(time.time()), "chat": {"id": OWNER, "type": "private"},
                         "from": {"id": OWNER}, "text": "plain note forwarded", "forward_date": 1}})
        self.assertEqual(self.store.pending_count(), 1)
        self.app.handle({"update_id": 2, "message": {"message_id": 901, "date": int(time.time()), "chat": {"id": OWNER, "type": "private"},
                         "from": {"id": OWNER}, "text": "/report"}})
        self.assertEqual(self.store.get("flush"), "1"); self.assertEqual(self.tg.sent[-1][0], "msg")


class AgentDecisionTests(unittest.TestCase):
    def test_free_text_report_request_flushes_without_commands(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Config(data_dir=d, owner_id=str(OWNER), anthropic_key="k"); st = Store(d); tg = FakeTG()
            st.add_item({"chat": OWNER, "msg_id": 1, "date": 1, "origin": "private", "kind": "text", "file_id": "", "filename": "", "size": 0, "duration": 0, "text": "x"})
            class A:
                def decide(self, q, reports, pending): return {"action": "report_now", "reply": ""}
            Pipeline(cfg, st, tg, FakeMedia(), A()).answer_owner("خب دیگه گزارش بده")
            self.assertEqual(st.get("flush"), "1"); self.assertTrue(tg.sent[-1][2])


class WebhookTests(unittest.TestCase):
    def test_signature_and_branch_filter(self):
        import hashlib, hmac as hm, json as js, threading, urllib.request, urllib.error
        from http.server import ThreadingHTTPServer
        calls = []
        srv = ThreadingHTTPServer(("127.0.0.1", 0), main.health("t", "s3cret", lambda: calls.append(1)))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        def post(ref, secret="s3cret", event="push"):
            body = js.dumps({"ref": ref}).encode()
            sig = "sha256=" + hm.new(secret.encode(), body, hashlib.sha256).hexdigest()
            req = urllib.request.Request(f"http://127.0.0.1:{srv.server_port}/github", data=body,
                                         headers={"X-Hub-Signature-256": sig, "X-GitHub-Event": event})
            try: return urllib.request.urlopen(req).status
            except urllib.error.HTTPError as e: return e.code
        self.assertEqual(post("refs/heads/other"), 200); self.assertEqual(calls, [])
        self.assertEqual(post("refs/heads/hamdel-claude-bot", secret="wrong"), 403); self.assertEqual(calls, [])
        self.assertEqual(post("refs/heads/hamdel-claude-bot"), 200); self.assertEqual(calls, [1])
        srv.shutdown()


class GeminiFallbackTests(unittest.TestCase):
    def test_retired_model_falls_back_and_is_remembered(self):
        import io, urllib.error
        from hamclaude.media import Media
        urls = []
        class R:
            def __enter__(s): return s
            def __exit__(s, *a): pass
            def read(s): return json.dumps({"candidates": [{"content": {"parts": [{"text": "متن"}]}}]}).encode()
        def opener(req, timeout=0):
            urls.append(req.full_url)
            if "gemini-2.5-flash" in req.full_url:
                raise urllib.error.HTTPError(req.full_url, 404, "gone", {}, io.BytesIO(b"{}"))
            return R()
        with tempfile.TemporaryDirectory() as d:
            seg = Path(d) / "a_a000.mp3"; seg.write_bytes(b"x")
            m = Media(Config(gemini_key="G", gemini_model="gemini-2.5-flash"), opener=opener, run=lambda *a, **k: None)
            m.audio_segments = lambda p, f, seconds=90: [seg]
            self.assertEqual(m.transcribe(Path(d) / "a.ogg", d), "متن"); self.assertEqual(m._gemini_ok, "gemini-3.8-flash")
            n = len(urls); m.transcribe(Path(d) / "a.ogg", d); self.assertEqual(len(urls) - n, 1)
