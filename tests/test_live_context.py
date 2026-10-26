import unittest
from hamdel.live_context import RecentContext, needs_history

class SeedAgeingTests(unittest.TestCase):
    def test_seed_expires_during_long_uptime(self):
        live=RecentContext()
        live.seed(5,[{'id':1,'date':1000,'text':'قدیمی'}],now=1000)
        self.assertEqual(len(live.snapshot(5,now=1000+59*24*3600)['messages']),1)
        self.assertEqual(live.snapshot(5,now=1000+61*24*3600)['messages'],[])

class ContextTests(unittest.TestCase):
    def test_simple_conversation_does_not_use_archive(self):
        for q in ('سلام','متن پوستر رو قشنگ‌تر کن','برای برنامه فردا چه پیشنهادی داری'):
            self.assertFalse(needs_history(q),q)
    def test_historical_queries_use_archive(self):
        for q in ('گزارش کمک‌های این ماه','اکسل پرداخت‌های مهر','جمع واریزها چقدر شد؟','آرشیو این گروه'):
            self.assertTrue(needs_history(q),q)
    def test_recent_group_context_bounded_and_scoped(self):
        live=RecentContext(limit=3,max_age=30)
        for i,c,text in [(1,99,'مراسم فردا'),(2,99,'کمک برای نیازمندان'),(3,88,'درباره گروه دیگر')]:
            live.add(c,{'message_id':i,'from':{'id':i},'text':text},now=1000)
        self.assertEqual([m['id'] for m in live.snapshot(99,now=1000)['messages']],[1,2])
        self.assertEqual([m['id'] for m in live.snapshot(88,now=1000)['messages']],[3])
        self.assertEqual(len(live.snapshot(99,now=1000)['category_hints']),2)
    def test_edited_message_replaces_old_version(self):
        live=RecentContext()
        live.add(99,{'message_id':5,'from':{'id':1},'text':'اول'},now=100)
        live.add(99,{'message_id':5,'from':{'id':1},'text':'ویرایش'},now=101)
        self.assertEqual([m['text'] for m in live.snapshot(99,now=101)['messages']],['ویرایش'])
    def test_normal_question_skips_archive_select(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        import main
        with tempfile.TemporaryDirectory() as directory, patch.object(main,'ROOT',Path(directory)):
            main.init()
            main.set_setting('chat_id','99')
            context=[]
            def capture(chat,question,c,jobid):
                context.append(c)
                return ('جواب کوتاه','')
            with patch('agent_bridge.reply',side_effect=capture):
                self.assertEqual(main.answer_ai(42,'به نظرت برای جشن چه کار کنیم؟',12),('جواب کوتاه',''))
            self.assertEqual(context[0]['mode'],'conversation')
            self.assertEqual(context[0]['archive_index'],[])
            self.assertIsNone(context[0]['total_archived'])

    def test_context_expires_and_does_not_invent_voice(self):
        live=RecentContext(max_age=8)
        live.add(99,{'message_id':1,'from':{'id':1},'voice':{'file_id':'x'}},now=100)
        self.assertIn('تأییدشده',live.snapshot(99,now=100)['messages'][0]['text'])
        self.assertEqual(live.snapshot(99,now=200)['messages'],[])

    def test_seeded_approved_messages_survive_restart_without_per_turn_db(self):
        from unittest.mock import patch
        from pathlib import Path
        import tempfile
        import main
        from hamdel.live_context import RecentContext
        import time
        now=int(time.time())
        with tempfile.TemporaryDirectory() as d, patch.object(main,'ROOT',Path(d)):
            with patch.object(main,'LIVE_GROUP',RecentContext()):
                main.init()
                main.set_setting('chat_id','99')
                with main.db() as c:
                    c.execute("""INSERT INTO messages
                        (chat,id,date,text,kind,status,source_username)
                        VALUES(99,101,?,'نیاز به کمک برای برنامه اطعام','text','text','motahari58')""",(now-100,))
                    c.execute("""INSERT INTO messages
                        (chat,id,date,text,kind,status,source_username)
                        VALUES(99,102,?,'پیام غیرمجاز','text','text',NULL)""",(now-99,))
                main.seed_live_group_once()
                self.assertEqual(main.STATE['group_seeded_messages'],1)
                rows=main.LIVE_GROUP.snapshot(99)['messages']
                self.assertEqual([r['id'] for r in rows],[101])
                self.assertEqual(rows[0]['source'],'verified_archive_seed')
                captured=[]
                with patch('agent_bridge.reply',side_effect=lambda chat,question,ctx,jobid:(captured.append(ctx) or ('پاسخ',''))):
                    self.assertEqual(main.answer_ai(42,'موضوعات گروه چیست؟',99)[0],'پاسخ')
                self.assertEqual(captured[0]['mode'],'conversation')
                self.assertEqual(captured[0]['group_context']['messages'][0]['id'],101)

    def test_live_messages_override_seed_without_duplication(self):
        live=RecentContext()
        now=10000000
        live.seed(99,[{'id':5,'date':now-20,'text':'نسخه قدیمی'}],now=now)
        live.add(99,{'message_id':5,'date':now-10,'from':{'id':58},'text':'نسخه جدید'},now=now)
        snap=live.snapshot(99,now=now)
        self.assertEqual(len(snap['messages']),1)
        self.assertEqual(snap['messages'][0]['text'],'نسخه جدید')

    def test_seed_expiry_only_allows_recent_records(self):
        live=RecentContext()
        now=10000000
        count=live.seed(99,[{'id':1,'date':now-120*86400,'text':'خیلی قدیمی'},
                            {'id':2,'date':now-25,'text':'جدید'}],now=now)
        self.assertEqual(count,1)
        self.assertEqual([r['id'] for r in live.snapshot(99,now=now)['messages']],[2])
