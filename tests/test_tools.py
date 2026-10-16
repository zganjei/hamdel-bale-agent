import json,tempfile,unittest,subprocess,sys
from pathlib import Path
from unittest.mock import patch
from hamdel.archive_tool import search
from hamdel.codex_agent import CodexAgent,AgentError
from hamdel.store import Store
from hamdel.speech import transcribe,SpeechUnavailable
class ToolTests(unittest.TestCase):
 def test_archive_date_filter_and_bounded_results(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'index.json';p.write_text(json.dumps([{'id':i,'date_utc':'2026-10-15 10:00','text':'اطعام '+'طولانی'*1000} for i in range(10)]))
   r=search(p,'اطعام','2026-10-01','2026-10-31',2)
   self.assertEqual(r['matched'],10);self.assertEqual(r['returned'],2)
   self.assertLessEqual(len(r['sources'][0]['text']),1800)
   self.assertEqual(search(p,start='2026-11-01')['matched'],0)
 def test_artifact_cannot_escape_workspace_or_expose_context(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);s=Store(root/'ledger');a=CodexAgent(root/'work',s)
   secret=root/'private.txt';secret.write_text('private')
   (a.workspace/'outputs'/'leak.txt').symlink_to(secret)
   def run(cmd,**kw):
    Path(cmd[cmd.index('--output-last-message')+1]).write_text(json.dumps({'answer':'test','draft_json':'','artifacts':['outputs/leak.txt']}))
    return type('Result',(),{'returncode':0,'stdout':''})()
   with patch('hamdel.codex_agent.subprocess.run',side_effect=run):
    with self.assertRaises(AgentError):a.ask('1','test','event')
 def test_usage_recorded_and_valid_artifact_returned(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);s=Store(root/'ledger');a=CodexAgent(root/'work',s)
   def run(cmd,**kw):
    (a.workspace/'outputs'/'report.csv').write_text('amount\n200\n')
    Path(cmd[cmd.index('--output-last-message')+1]).write_text(json.dumps({'answer':'آماده شد','draft_json':'','artifacts':['outputs/report.csv']}))
    return type('Result',(),{'returncode':0,'stdout':json.dumps({'type':'turn.completed','usage':{'input_tokens':10,'output_tokens':3}})})()
   with patch('hamdel.codex_agent.subprocess.run',side_effect=run):a.ask('1','test','event')
   self.assertEqual(len(a.last_artifacts),1)
   with s.connect() as c:self.assertEqual(c.execute('select count(*) from usage_events').fetchone()[0],1)
 def test_unconfigured_speech_does_not_emit_garbage(self):
  with patch('hamdel.speech.settings',return_value={'speech_provider':'disabled'}):
   with self.assertRaises(SpeechUnavailable):transcribe(Path('audio.opus'),Path('.'),Path('whisper'))
 def test_mcp_search_calculate_and_export_with_no_shell(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);s=Store(root/'ledger');a=CodexAgent(root/'work',s)
   context=a.workspace/'context';context.mkdir()
   (context/'archive_index.json').write_text(json.dumps([{'id':12,'date_utc':'2026-10-15','text':'کلاس آزمایشی'}]))
   messages=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{}},
      {'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'search_archive','arguments':{'query':'کلاس'}}},
      {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'calculate','arguments':{'expression':'17*23'}}},
      {'jsonrpc':'2.0','id':4,'method':'tools/call','params':{'name':'create_file','arguments':{'filename':'result.csv','content':'result\n391\n'}}},
      {'jsonrpc':'2.0','id':5,'method':'tools/call','params':{'name':'create_file','arguments':{'filename':'../../bad.txt','content':'bad'}}},
      {'jsonrpc':'2.0','id':6,'method':'tools/call','params':{'name':'calculate','arguments':{'expression':'__import__("os").getcwd()'}}}]
   proc=subprocess.run([sys.executable,str(a.workspace/'tools/mcp_server.py'),str(a.workspace)],input='\n'.join(json.dumps(x) for x in messages),text=True,capture_output=True,check=True)
   responses=[json.loads(line) for line in proc.stdout.splitlines()]
   self.assertEqual(json.loads(responses[1]['result']['content'][0]['text'])['sources'][0]['id'],12)
   self.assertEqual(json.loads(responses[2]['result']['content'][0]['text'])['result'],391)
   self.assertTrue((a.workspace/'outputs/result.csv').is_file())
   self.assertTrue(responses[4]['result']['isError']);self.assertTrue(responses[5]['result']['isError'])
