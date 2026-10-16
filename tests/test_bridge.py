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

 def test_group_voice_archived_without_transcription_or_external_processing(self):
  voice={'message_id':61,'chat':{'id':99,'type':'group'},'from':{'id':123,'username':'motahari58'},'voice':{'file_id':'sample','file_size':100,'duration':4}}
  main.handle({'update_id':42,'message':voice})
  with main.db() as c:
   self.assertEqual(c.execute('select count(*) from speech_jobs').fetchone()[0],0)
   self.assertEqual(c.execute('select kind from messages where chat=99 and id=61').fetchone()[0],'voice')
 def test_private_voice_raw_uses_original_transcript(self):
  with main.db() as c:
   c.execute("INSERT INTO speech_jobs(chat,id,file_id,filename,size,duration,caption,reply,status,transcript,created) VALUES(42,17,'file','a.ogg',10,3,'',1,'done','متن خام از صدای بله',1)")
  with patch.object(main,'api') as api:
   agent_bridge.voice_ready(42,17,'متن خام از صدای بله',suggested='متن ویراسته')
   data=api.call_args.args[1]['reply_markup']['inline_keyboard'][1][0]['callback_data']
   with patch.object(main,'send') as send:
    agent_bridge.callback({'id':'cb','from':{'id':42},
       'message':{'chat':{'id':42}},'data':data},20)
    self.assertIn('متن خام از صدای بله',send.call_args.args[1])

 def test_voice_polish_does_not_store_explanatory_header(self):
  with patch.object(main,'api') as api:
   agent_bridge.voice_ready(42,92,'متن خام',suggested='متن خواناتر')
   msg=api.call_args.args[1]
   self.assertIn('متن خواناتر',msg['text'])
   self.assertNotIn('[ویرایش پیشنهادی',msg['text'])
   self.assertNotIn('نامفهوم',msg['text'])
   action=msg['reply_markup']['inline_keyboard'][0][0]['callback_data']
   vid=action.split(':',1)[1]
   self.assertEqual(agent_bridge.store().get_voice(vid,42)['transcript'],'متن خواناتر')


 def test_only_target_user_archived(self):
  other={'message_id':64,'chat':{'id':99,'type':'group'},'from':{'id':77,'username':'someone_else'},'text':'other'}
  main.handle({'update_id':110,'message':other})
  with main.db() as c:
   self.assertEqual(c.execute('SELECT count(*) FROM messages').fetchone()[0],0)
   self.assertEqual(c.execute('SELECT count(*) FROM updates').fetchone()[0],0)
  target={'message_id':65,'chat':{'id':99,'type':'group'},'from':{'id':58,'username':'Motahari58'},'text':'گزارش همدل'}
  main.handle({'update_id':111,'message':target})
  with main.db() as c:
   row=c.execute('SELECT source_username,source_id,source_kind,text FROM messages').fetchone()
   self.assertEqual(tuple(row),('motahari58',58,'user','گزارش همدل'))
   self.assertNotIn('گزارش همدل',c.execute('SELECT payload FROM updates').fetchone()[0])

 def test_known_id_continues_without_username(self):
  for mid, sender in [(70,{'id':58,'username':'motahari58'}),(71,{'id':58})]:
   main.handle({'update_id':mid,'message':{'message_id':mid,'chat':{'id':99,'type':'group'},'from':sender,'text':'test'}})
  with main.db() as c:self.assertEqual(c.execute('SELECT count(*) FROM messages').fetchone()[0],2)

 def test_forwarded_messages_not_archived(self):
  m={'message_id':80,'chat':{'id':99,'type':'group'},'from':{'id':999,'username':'someone_else'},
     'forward_from':{'id':58,'username':'motahari58'},
     'reply_to_message':{'from':{'id':58,'username':'motahari58'}},'text':'forward'}
  main.handle({'update_id':180,'message':m})
  with main.db() as c:self.assertEqual(c.execute('SELECT count(*) FROM messages').fetchone()[0],0)

 def test_sender_chat_overrides_false_from(self):
  m={'message_id':90,'chat':{'id':99,'type':'group'},'from':{'id':999},
     'sender_chat':{'id':1001,'username':'motahari58'},'text':'channel'}
  main.handle({'update_id':190,'message':m})
  m['message_id']=91;m['sender_chat']={'id':1002,'username':'unrelated'}
  m['from']={'id':58,'username':'motahari58'}
  main.handle({'update_id':191,'message':m})
  with main.db() as c:
   self.assertEqual(c.execute('SELECT count(*) FROM messages').fetchone()[0],1)
   self.assertEqual(c.execute('SELECT source_kind FROM messages').fetchone()[0],'chat')

 def test_legacy_rows_preserved_but_hidden(self):
  with main.db() as c:c.execute("INSERT INTO messages(chat,id,date,text,kind,status) VALUES(99,901,1,'old','text','text')")
  m={'message_id':902,'chat':{'id':99,'type':'group'},'from':{'id':58,'username':'motahari58'},'text':'new'}
  main.handle({'update_id':202,'message':m})
  with patch.object(agent_bridge,'reply',return_value=('ok','')) as stub:
   main.answer_ai(42,'new',200)
  context=stub.call_args.args[2]
  self.assertEqual(context['total_archived'],1)
  self.assertEqual([r['id'] for r in context['archive_index']],[902])
  with main.db() as c:self.assertEqual(c.execute('SELECT count(*) FROM messages').fetchone()[0],2)

 def test_owner_mention_still_processed_without_archiving(self):
  m={'message_id':99,'chat':{'id':99,'type':'group'},'from':{'id':42,'username':'someone_else'},
     'text':'@hamdel_report_bot سلام'}
  main.handle({'update_id':209,'message':m})
  with main.db() as c:
   self.assertEqual(c.execute('SELECT count(*) FROM messages').fetchone()[0],0)
   self.assertEqual(c.execute('SELECT count(*) FROM ai_jobs').fetchone()[0],1)


 def test_private_photo_queued_without_blocking_poller(self):
  message={'message_id':5,'chat':{'id':42,'type':'private'},'from':{'id':42},'photo':[{'file_id':'example','file_size':100}]}
  main.handle({'update_id':11,'message':message})
  with main.db() as c:self.assertTrue(c.execute('select prompt from ai_jobs').fetchone()[0].startswith('__HAMDEL_MEDIA__'))
