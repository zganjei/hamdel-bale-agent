"""Regression for replacing stale Liara secrets without exposing values."""
import os
import unittest
from unittest.mock import patch, call
from scripts.sync_liara_speech_env import matches, reconcile

A = "0123456789abcdef0123456789abcdef"
NEW = {"CLOUDFLARE_ACCOUNT_ID": A, "CLOUDFLARE_API_TOKEN": "new-token"}
OLD = [{"key":"BALE_BOT_TOKEN","value":"do-not-log"},
       {"key":"CLOUDFLARE_ACCOUNT_ID","value":A},
       {"key":"CLOUDFLARE_API_TOKEN","value":"previous-token"},
       {"key":"CLOUDFLARE_API_TOKEN","value":"new-token"}]

class LiaraEnvTests(unittest.TestCase):
    def test_duplicate_prevents_false_success(self):
        self.assertFalse(matches(OLD,NEW))
        self.assertTrue(matches(OLD[:2]+[{"key":"CLOUDFLARE_API_TOKEN","value":"new-token"}],NEW))

    def test_reconciles_stale_duplicate_only(self):
        cleaned=[{"key":"BALE_BOT_TOKEN","value":"do-not-log"}]
        final=cleaned+[{"key":k,"value":v} for k,v in NEW.items()]
        with patch.dict(os.environ,NEW), patch("scripts.sync_liara_speech_env.list_env",side_effect=[OLD,cleaned,final]) as listing:
            with patch("scripts.sync_liara_speech_env.cli",return_value="") as cli:
                reconcile()
        self.assertEqual(cli.call_count,2)
        self.assertEqual(cli.call_args_list[0].args,("env","unset","CLOUDFLARE_ACCOUNT_ID","CLOUDFLARE_API_TOKEN","--force"))
        self.assertEqual(cli.call_args_list[1].args,("env","set","CLOUDFLARE_ACCOUNT_ID="+A,"CLOUDFLARE_API_TOKEN=new-token","--force"))


    def test_probe_exposes_sanitized_network_issue_only(self):
        import io
        import main
        with patch("hamdel.config.settings",return_value={"speech_provider":"cloudflare_free"}):
            with patch.dict(os.environ,NEW):
                with patch("scripts.check_cloudflare_speech.main",side_effect=lambda: (print("Cloudflare: HTTP 403 error codes [10000]") or 1)):
                    main.probe_speech_readiness()
                    self.assertEqual(main.STATE["speech_state"],"api_error")
                    self.assertEqual(main.STATE["speech_issue"],"workers_ai_http_403")

    def test_no_restart_when_already_correct(self):
        ready=[{"key":k,"value":v} for k,v in NEW.items()]
        with patch.dict(os.environ,NEW), patch("scripts.sync_liara_speech_env.list_env",return_value=ready):
            with patch("scripts.sync_liara_speech_env.cli") as cli:
                reconcile()
                cli.assert_not_called()

    def test_probe_reports_false_on_failure(self):
        import main
        with patch("hamdel.config.settings",return_value={"speech_provider":"cloudflare_free"}):
            with patch.dict(os.environ,NEW):
                with patch("scripts.check_cloudflare_speech.main",return_value=1):
                    main.probe_speech_readiness()
                    self.assertEqual(main.STATE["speech_state"],"api_error")
