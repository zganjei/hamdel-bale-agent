"""Regression: stored binaries must become report evidence without repeated AI calls."""
import os
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import main
import media_reader
import multimodal
import agent_bridge

class EvidenceTests(unittest.TestCase):
    def test_cache_reuse_and_report_context(self):
        with tempfile.TemporaryDirectory() as d, patch.object(main,'ROOT',Path(d)):
            main.init();main.set_setting('chat_id',123)
            p=Path(d)/'media/a.jpg';p.write_bytes(b'same content')
            with main.db() as c:
                for n in (1,2):
                    c.execute("INSERT INTO messages(chat,id,date,text,kind,file_id,filename,status,path) VALUES(123,?,1,'','photo',?,'a.jpg','saved','media/a.jpg')",(n,str(n)))
            with patch.object(media_reader,'analyze',return_value=('One image','visible classroom')) as analyze:
                self.assertTrue(main.process_media());self.assertTrue(main.process_media())
                self.assertFalse(main.process_media());self.assertEqual(analyze.call_count,1)
            with patch.object(agent_bridge,'reply',side_effect=lambda chat,question,context,jobid:context):
                context=main.answer_ai(999,'گزارش کلاس را بنویس',1)
                self.assertEqual(context['archive_coverage'],{'done':2})
                self.assertEqual(context['selected_reports'][0]['media_summary'],'visible classroom')
                self.assertEqual(len({r['content_hash'] for r in context['selected_reports']}),1)
                self.assertEqual(main.answer_ai(999,'سلام',2)['included'],0)

    def test_failed_media_is_not_claimed_read(self):
        with tempfile.TemporaryDirectory() as d, patch.object(main,'ROOT',Path(d)):
            main.init();main.set_setting('chat_id',123)
            (Path(d)/'media/a.jpg').write_bytes(b'bad image')
            with main.db() as c:
                c.execute("INSERT INTO messages(chat,id,date,text,kind,file_id,filename,status,path) VALUES(123,1,1,'','photo','f','a.jpg','saved','media/a.jpg')")
            with patch.object(media_reader,'analyze',side_effect=RuntimeError('failure')):
                main.process_media();self.assertFalse(main.process_media())
            with patch.object(agent_bridge,'reply',side_effect=lambda chat,question,context,jobid:context):
                context=main.answer_ai(999,'گزارش',3)
                self.assertEqual(context['archive_coverage'],{'failed':1})
                self.assertEqual(context['selected_reports'][0]['media_summary'],'')

if __name__=='__main__':unittest.main()
