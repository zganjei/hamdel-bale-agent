from __future__ import annotations
import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from hamdel.store import Store, validate
from hamdel.report import calculate
from hamdel.runner import HamdelService


def donation(ref='x1', amount=200):
    return {'kind': 'donation', 'title': 'کمک صدقات', 'body': '۲۰۰ کرون برای کد ۱۰',
            'data': {'date': '2026-10-15', 'amount': amount, 'currency': 'SEK',
                     'campaign_code': '10', 'payment_reference': ref, 'note': ''}}

class FakeBale:
    def __init__(self): self.messages=[]; self.callbacks=[]
    def send(self, chat, text, reply_markup=None): self.messages.append((chat,text,reply_markup))
    def answer_callback(self, cid, text=''): self.callbacks.append((cid,text))
    def get_file(self, file_id, target): target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(b'photo'); return target

class FakeAgent:
    def __init__(self, root): self.workspace=root
    def ask(self, chat, text, event, image=None):
        return 'یک پیش‌نویس مالی آماده کردم؛ لطفاً بررسی کن.', donation()

class Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.store=Store(self.root/'trusted')
        self.bale=FakeBale()
        self.service=HamdelService(bale=self.bale,store=self.store,
                    agent=FakeAgent(self.root/'workspace'),allowed_users={12345},asr=Mock())

    def test_draft_then_approved(self):
        update={'update_id':1,'message':{'message_id':77,'chat':{'id':321,'type':'private'},
                                         'from':{'id':12345},'text':'یک کمک ۲۰۰ کرونی برای کد ۱۰ ثبت کن'}}
        self.service.handle_update(update)
        self.assertEqual(self.store.summary()['total_approved_transactions'],0)
        pending=self.store.list_drafts()
        self.assertEqual(len(pending),1)
        draft_id=pending[0]['id']
        self.assertTrue(any('تأیید' in str(m[2]) for m in self.bale.messages if m[2]))
        self.assertTrue(any('مبلغ: 200 SEK' in m[1] for m in self.bale.messages))
        self.service.handle_update({'update_id':2,'callback_query':{'id':'cb','from':{'id':12345},
            'data':'approve:'+draft_id,'message':{'chat':{'id':321}}}})
        self.assertEqual(self.store.summary()['total_approved_transactions'],1)
        self.assertEqual(self.store.decide(draft_id,True),'approved')
        self.assertEqual(self.store.summary()['total_approved_transactions'],1)

    def test_approval_from_other_chat_blocked(self):
        did, _ = self.store.create_draft('bale:321:52', donation())
        self.service.handle_update({'update_id':55,'callback_query':{'id':'cb','from':{'id':12345},
            'data':'approve:'+did,'message':{'chat':{'id':999}}}})
        self.assertEqual(self.store.get_draft(did)['status'], 'pending')
        self.assertEqual(self.store.summary()['total_approved_transactions'],0)

    def test_duplicate_reference_normalizes_whitespace(self):
        x = donation(' abc ')
        y = donation('ABC')
        a, _ = self.store.create_draft('a', x)
        b, _ = self.store.create_draft('b', y)
        self.assertEqual(self.store.decide(a, True),'approved')
        self.assertEqual(self.store.decide(b, True),'duplicate_reference')

    def test_duplicate_payment_reference_blocks(self):
        id1,_=self.store.create_draft('bale:1:1',donation('r1'))
        id2,_=self.store.create_draft('bale:1:2',donation('r1'))
        self.assertEqual(self.store.decide(id1,True),'approved')
        self.assertEqual(self.store.decide(id2,True),'duplicate_reference')
        self.assertEqual(self.store.summary()['total_approved_transactions'],1)

    def test_irrelevant_currency_unit_rejected(self):
        pay=donation()
        pay['data']['currency']='SEK or toman'
        with self.assertRaises(ValueError): validate(pay)
        pay=donation()
        pay['data']['amount']='200'
        with self.assertRaises(ValueError): validate(pay)

    def test_unauthorized_user_ignored(self):
        self.service.handle_update({'update_id':9,'message':{'message_id':1,'chat':{'id':9,'type':'private'},'from':{'id':99999},'text':'ثبت کن'}})
        self.assertEqual(len(self.store.list_drafts()),0)
        self.assertEqual(len(self.bale.messages),0)

    def test_unauthorized_callback_cannot_approve(self):
        did,_=self.store.create_draft('bale:1:3', donation())
        self.service.handle_update({'update_id':10,'callback_query':{'id':'x','from':{'id':99999},
            'data':'approve:'+did,'message':{'chat':{'id':321}}}})
        self.assertEqual(self.store.get_draft(did)['status'],'pending')

    def test_update_is_idempotent(self):
        update={'update_id':23,'message':{'message_id':77,'chat':{'id':321,'type':'private'},'from':{'id':12345},'text':'درخواست'}}
        self.service.handle_update(update)
        n=len(self.bale.messages)
        self.service.handle_update(update)
        self.assertEqual(n,len(self.bale.messages))
        self.assertEqual(len(self.store.list_drafts()),1)

    def test_exact_report_only_approved(self):
        first,_=self.store.create_draft('1', donation('p1',200))
        self.store.decide(first,True)
        self.store.create_draft('2', donation('p2',700))
        csvp=self.root/'approved.csv'
        self.store.export_approved_csv(csvp)
        r=calculate(csvp,'2026-10-01','2026-10-31',26500)
        self.assertEqual(r['count'],1)
        self.assertEqual(r['summary'][0]['amount'],200)
        self.assertEqual(r['sek_to_toman']['toman_equivalent'],5300000)

    def test_rejected_draft_not_counted(self):
        did,_=self.store.create_draft('3',donation())
        self.assertEqual(self.store.decide(did,False),'rejected')
        self.assertEqual(self.store.summary()['total_approved_transactions'],0)

    def test_approved_content_exportable(self):
        did,_ = self.store.create_draft('xcontent', {
            'kind':'aid_request','title':'کمک غذایی','body':'کمک برای خانواده','data':{'campaign_code':'2'}})
        self.assertEqual(self.store.approved_content(), [])
        self.store.decide(did, True)
        items = self.store.approved_content()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['title'], 'کمک غذایی')
        self.service.snapshot()
        archived = json.loads((self.root/'workspace/context/approved_content.json').read_text())
        self.assertEqual(archived[0]['id'], did)

    def test_voice_needs_confirmation_and_rejection(self):
        self.service.asr.transcribe.return_value = 'یک کمک پنجاه کرونی برای کد ده ثبت کن'
        self.service.handle_update({'update_id':61,'message':{'message_id':61,
            'chat':{'id':321,'type':'private'},'from':{'id':12345},'voice':{'file_id':'audio1'}}})
        self.assertEqual(self.store.list_drafts(), [])
        self.assertEqual(self.store.summary()['total_approved_transactions'], 0)
        buttons = self.bale.messages[-1][2]['inline_keyboard'][0]
        vid = buttons[0]['callback_data'].split(':')[1]
        self.service.handle_update({'update_id':62,'callback_query':{'id':'v2','from':{'id':12345},
            'data':'voice_no:'+vid,'message':{'chat':{'id':321}}}})
        self.assertEqual(self.store.list_drafts(), [])
        self.assertEqual(self.store.get_voice(vid,321)['status'], 'rejected')

    def test_voice_confirm_then_financial_draft(self):
        self.service.asr.transcribe.return_value = 'برای کد ۱۰ مبلغ ۲۰۰ کرون ثبت کن'
        self.service.handle_update({'update_id':71,'message':{'message_id':71,
            'chat':{'id':321,'type':'private'},'from':{'id':12345},'voice':{'file_id':'audio1'}}})
        confirm = self.bale.messages[-1][2]['inline_keyboard'][0][0]['callback_data']
        self.assertEqual(len(self.store.list_drafts()), 0)
        self.service.handle_update({'update_id':72,'callback_query':{'id':'v3','from':{'id':12345},
            'data':confirm,'message':{'chat':{'id':321}}}})
        self.assertEqual(len(self.store.list_drafts()), 1)
        self.assertEqual(self.store.summary()['total_approved_transactions'], 0)
        self.assertEqual(self.store.get_voice(confirm.split(':')[1],321)['status'],'processed')

    def test_voice_cannot_confirm_from_other_chat(self):
        self.store.add_voice('bale:321:23',321,'سلام')
        voice = self.store.add_voice('bale:321:24',321,'متن مهم')
        self.service.handle_update({'update_id':90,'callback_query':{'id':'v4','from':{'id':12345},
            'data':'voice_ok:'+voice,'message':{'chat':{'id':999}}}})
        self.assertEqual(self.store.get_voice(voice,321)['status'], 'pending')
        self.assertEqual(self.store.list_drafts(), [])

    def test_publisher_not_implemented(self):
        from hamdel.publisher import TelegramPublisher
        with self.assertRaises(NotImplementedError): TelegramPublisher().publish({},'channel')

if __name__=='__main__':unittest.main()
