import json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import main, agent_bridge, multimodal
from hamdel.store import Store


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);old=main.ROOT;main.ROOT=self.root
        self.addCleanup(setattr,main,'ROOT',old)
        main.init();main.set_setting('owner_id','42');main.set_setting('chat_id','-99')
        from hamdel.live_context import RecentContext
        patcher=patch.object(main,'LIVE_GROUP',RecentContext());patcher.start();self.addCleanup(patcher.stop)

    def test_group_posts_with_null_sender_or_reply_do_not_raise_and_reach_context(self):
        group={'id':-99,'type':'supergroup'}
        updates=[
            {'update_id':1,'message':{'message_id':1,'date':main.time.time(),'chat':group,'from':None,'sender_chat':{'id':-99},'text':'پست ناشناس'}},
            {'update_id':2,'message':{'message_id':2,'date':main.time.time(),'chat':group,'from':{'id':7,'username':'ali'},'text':'پیام عضو','reply_to_message':{'from':None}}},
        ]
        for u in updates: main.handle(u)
        texts=[m['text'] for m in main.LIVE_GROUP.snapshot(-99)['messages']]
        self.assertEqual(texts,['پست ناشناس','پیام عضو'])
        main.handle({'update_id':3,'message':{'message_id':3,'chat':None,'text':'x'}})  # must not raise

    def test_failing_update_is_retried_then_skipped_not_blocking(self):
        calls=[]
        def flaky(u):
            calls.append(u['update_id'])
            if u['update_id']==1 and calls.count(1)<2: raise RuntimeError('temporary')
            if u['update_id']==2: raise ValueError('poison')
        with patch.object(main,'handle',side_effect=flaky), patch.object(main.time,'sleep'):
            self.assertTrue(main.dispatch_update({'update_id':1}))
            self.assertFalse(main.dispatch_update({'update_id':2}))
        self.assertEqual(calls,[1,1,2,2,2])
        self.assertEqual(main.STATE['skipped_updates_since_boot'],1)
        self.assertEqual(main.STATE['last_handler_error'],'ValueError')

    def test_other_chat_updates_counted_separately(self):
        before=main.STATE['group_other_chat_updates']
        main.handle({'update_id':9,'message':{'message_id':1,'chat':{'id':-5,'type':'group'},'from':{'id':1},'text':'x'}})
        self.assertEqual(main.STATE['group_other_chat_updates'],before+1)


class VoiceReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);old=main.ROOT;main.ROOT=self.root
        self.addCleanup(setattr,main,'ROOT',old)
        main.init();main.set_setting('owner_id','42');main.set_setting('chat_id','99')
        agent_bridge._store=Store(self.root/'trusted');self.addCleanup(setattr,agent_bridge,'_store',None)
        self.long=' '.join(f'کلمه{i}' for i in range(1500))  # well over one message

    def test_chunks_lose_no_words(self):
        parts=agent_bridge.chunks(self.long)
        self.assertGreater(len(parts),1)
        self.assertTrue(all(len(p)<=3000 for p in parts))
        self.assertEqual(' '.join(parts).split(),self.long.split())

    def test_whole_transcript_shown_before_buttons_and_approval_runs_all_of_it(self):
        with patch.object(main,'api') as api:
            agent_bridge.voice_ready(42,5,self.long)
        sent=[c.args[1] for c in api.call_args_list]
        self.assertGreater(len(sent),1)
        self.assertTrue(all('reply_markup' not in m for m in sent[:-1]))
        self.assertIn('reply_markup',sent[-1])
        shown=' '.join(m['text'] for m in sent)
        self.assertTrue(all(f'کلمه{i}' in shown for i in (0,700,1499)))
        data=sent[-1]['reply_markup']['inline_keyboard'][0][0]['callback_data']
        with patch.object(main,'api'):
            agent_bridge.callback({'id':'c','from':{'id':42},'message':{'chat':{'id':42}},'data':data},1)
        with main.db() as c:
            prompt=c.execute('select prompt from ai_jobs').fetchone()[0]
        self.assertEqual(prompt.split(),self.long.split())

    def test_partial_send_failure_never_offers_or_allows_approval(self):
        calls=[]
        def api(method,payload=None):
            calls.append(payload)
            if len(calls)>=3: raise RuntimeError('bale down')
        with patch.object(main,'api',side_effect=api):
            with self.assertRaises(RuntimeError):
                agent_bridge.voice_ready(42,6,self.long)
        self.assertTrue(all('reply_markup' not in c for c in calls))
        with agent_bridge.store().connect() as db:
            vid,status=db.execute('select id,status from voice_confirmations').fetchone()
        self.assertEqual(status,'rejected')
        with patch.object(main,'api'):
            agent_bridge.callback({'id':'c','from':{'id':42},'message':{'chat':{'id':42}},'data':'voice_ok:'+vid},2)
        with main.db() as c: self.assertEqual(c.execute('select count(*) from ai_jobs').fetchone()[0],0)

    def test_replay_shows_stored_text_and_decided_voice_is_not_reoffered(self):
        with patch.object(main,'api') as api:
            agent_bridge.voice_ready(42,7,'متن اول')
            agent_bridge.voice_ready(42,7,'متن دوم متفاوت')
        self.assertIn('متن اول',api.call_args.args[1]['text'])
        self.assertNotIn('متن دوم',api.call_args.args[1]['text'])
        data=api.call_args.args[1]['reply_markup']['inline_keyboard'][0][0]['callback_data']
        with patch.object(main,'api'):
            agent_bridge.callback({'id':'c','from':{'id':42},'message':{'chat':{'id':42}},'data':data},3)
        with patch.object(main,'api') as api2:
            agent_bridge.voice_ready(42,7,'متن اول')
        api2.assert_not_called()

    def test_raw_whisper_text_is_sent_in_full(self):
        with main.db() as c:
            c.execute("INSERT INTO speech_jobs(chat,id,file_id,filename,size,duration,caption,reply,status,transcript,created) VALUES(42,8,'f','a.ogg',1,1,'',1,'done',?,1)",(self.long,))
        with patch.object(main,'api') as api:
            agent_bridge.voice_ready(42,8,self.long,suggested='ویراسته')
            data=api.call_args.args[1]['reply_markup']['inline_keyboard'][1][0]['callback_data']
        with patch.object(main,'send') as send, patch.object(main,'api'):
            agent_bridge.callback({'id':'c','from':{'id':42},'message':{'chat':{'id':42}},'data':data},4)
        text=' '.join(c.args[1] for c in send.call_args_list)
        self.assertIn('کلمه1499',text)


class ProofreadModelTests(unittest.TestCase):
    def run_codex(self,**kwargs):
        captured={}
        def fake_run(cmd,**kw):
            captured['cmd']=cmd
            out=Path(cmd[cmd.index('-o')+1])
            out.write_text(json.dumps({'text':'x','image_prompt':''}))
            return type('R',(),{'returncode':0})()
        with tempfile.TemporaryDirectory() as d, patch.object(multimodal.subprocess,'run',side_effect=fake_run):
            try: multimodal.codex('p',Path(d),**kwargs)
            except Exception: pass
        return captured['cmd']

    def test_text_call_pins_model_and_effort(self):
        cmd=self.run_codex(model='gpt-6-luna',effort='low')
        self.assertEqual(cmd[cmd.index('-m')+1],'gpt-6-luna')
        self.assertIn('model_reasoning_effort="low"',cmd)
        self.assertIn('image_generation',cmd)  # still disabled for text

    def test_image_generation_path_is_untouched(self):
        cmd=self.run_codex(image=True,model='gpt-6-luna',effort='low')
        self.assertNotIn('-m',cmd)
        self.assertNotIn('model_reasoning_effort="low"',cmd)
        self.assertNotIn('--disable image_generation',' '.join(cmd))


if __name__=='__main__': unittest.main()
