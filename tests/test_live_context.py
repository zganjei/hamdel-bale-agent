import unittest
from hamdel.live_context import RecentContext, needs_history

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
