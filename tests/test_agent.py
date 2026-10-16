from __future__ import annotations
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from hamdel.store import Store
from hamdel.codex_agent import CodexAgent

class AgentTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        root = Path(self.root.name)
        self.store = Store(root / 'trusted')
        self.agent = CodexAgent(root / 'workspace', self.store)

    def test_session_resume_without_leaking_bale_token(self):
        commands = []
        envs = []
        tid = '7f9f9a2e-1b3c-4c7a-9b0e-000000000001'
        def run(cmd, **kw):
            commands.append(cmd)
            envs.append(kw['env'])
            Path(cmd[cmd.index('--output-last-message') + 1]).write_text(json.dumps({'answer':'آماده شد.', 'draft_json':''}), encoding='utf-8')
            return type('Result', (), {'returncode': 0, 'stdout': json.dumps({'type':'thread.started','thread_id':tid}), 'stderr':''})()
        with patch('hamdel.codex_agent.subprocess.run', side_effect=run), patch.dict(os.environ, {'BALE_BOT_TOKEN':'TOP_SECRET', 'OPENAI_API_KEY':'API_SECRET'}):
            answer, draft = self.agent.ask('123', 'سلام', 'event1')
            answer2, draft2 = self.agent.ask('123', 'گزارش بده', 'event2')
        self.assertEqual(answer, 'آماده شد.')
        self.assertEqual(answer2, 'آماده شد.')
        self.assertIsNone(draft)
        self.assertIsNone(draft2)
        self.assertEqual(self.store.get_thread('123'), tid)
        self.assertNotIn('resume', commands[0])
        self.assertIn('resume', commands[1])
        self.assertIn(tid, commands[1])
        self.assertEqual(commands[0][commands[0].index('-m') + 1], 'gpt-6-luna')
        for env in envs:
            self.assertNotIn('BALE_BOT_TOKEN', env)
            self.assertNotIn('OPENAI_API_KEY', env)
        self.assertTrue((self.agent.workspace/'tools/report.py').is_file())
        self.assertTrue((self.agent.workspace/'answer.schema.json').is_file())
        for command in commands:
            self.assertIn('read-only', command)
            self.assertIn('shell_tool', command)
            self.assertIn('--output-schema', command)
            self.assertNotIn('danger-full-access', command)

    def test_invalid_agent_json_never_saved_as_financial_draft(self):
        tid = '7f9f9a2e-1b3c-4c7a-9b0e-000000000002'
        def run(cmd, **kw):
            Path(cmd[cmd.index('--output-last-message') + 1]).write_text(
                json.dumps({'answer':'ثبت کردم', 'draft_json':'{bad'}), encoding='utf-8')
            return type('Result', (), {'returncode':0,'stdout':json.dumps({'type':'thread.started','thread_id':tid}),'stderr':''})()
        with patch('hamdel.codex_agent.subprocess.run', side_effect=run):
            with self.assertRaises(Exception):
                self.agent.ask('42','ثبت کن','id')
        self.assertIsNone(self.store.get_thread('42'))

if __name__ == '__main__': unittest.main()
