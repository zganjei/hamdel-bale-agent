import tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import main,agent_bridge
from hamdel.store import Store
class BridgeTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name);self.old=main.ROOT;main.ROOT=self.root;self.addCleanup(setattr,main,'ROOT',self.old)
  main.init();main.set_setting('owner_id','42');main.set_setting('chat_id','99')
  agent_bridge._store=Store(self.root/'trusted');self.addCleanup(setattr,agent_bridge,'_store',None)
 def test_voice_requires_approval_and_replay_does_not_duplicate(self):
  with patch.object(main,'api') as api:
   agent_bridge.voice_ready(42,3,'متن صوت آزمایشی')
   with main.db() as c:self.assertEqual(c.execute('select count(*) from ai_jobs').fetchone()[0],0)
   data=api.call_args.args[1]['reply_markup']['inline_keyboard'][0][0]['callback_data']
   cb={'id':'cb','from':{'id':42},'message':{'chat':{'id':42}},'data':data}
   agent_bridge.callback(cb,10);agent_bridge.callback(cb,11)
   with main.db() as c:self.assertEqual(c.execute('select count(*) from ai_jobs').fetchone()[0],1)
 def test_unauthorized_and_cross_chat_approval_denied(self):
  s=agent_bridge.store();did,_=s.create_draft('bale:42:job:1',{'kind':'donation','title':'test','body':'test','data':{'date':'2026-10-16','amount':200,'currency':'SEK'}})
  with patch.object(main,'api'):
   agent_bridge.callback({'id':'cb','from':{'id':5},'message':{'chat':{'id':42}},'data':'approve:'+did},1)
   agent_bridge.callback({'id':'cb','from':{'id':42},'message':{'chat':{'id':88}},'data':'approve:'+did},2)
  self.assertEqual(s.get_draft(did)['status'],'pending')
 def test_opus_document_queued_as_voice(self):
  message={'message_id':5,'chat':{'id':42,'type':'private'},'from':{'id':42},'document':{'file_id':'example','file_name':'note.opus','file_size':100}}
  main.handle({'update_id':11,'message':message})
  with main.db() as c:self.assertEqual(c.execute('select count(*) from speech_jobs').fetchone()[0],1)
 def test_private_photo_queued_without_blocking_poller(self):
  message={'message_id':5,'chat':{'id':42,'type':'private'},'from':{'id':42},'photo':[{'file_id':'example','file_size':100}]}
  main.handle({'update_id':11,'message':message})
  with main.db() as c:self.assertTrue(c.execute('select prompt from ai_jobs').fetchone()[0].startswith('__HAMDEL_MEDIA__'))
