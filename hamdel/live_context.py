"""Short-lived group context for normal chat, not the permanent donation archive."""
from collections import OrderedDict
from threading import RLock
import time

def needs_history(question):
    q=str(question or '').casefold().replace('ي','ی').replace('ك','ک')
    if any(t in q for t in ('آرشیو','سوابق','سابقه','دیتابیس','پایگاه داده','اکسل','xlsx','csv','صورت‌حساب','صورت حساب')):
        return True
    periods=('ماه','هفته','سال','دوره','بازه','از تاریخ','تا تاریخ','از اول','کل','قبلی','گذشته',
             'مهر','شهریور','آبان','آذر','بهمن','اسفند','فروردین','اردیبهشت','خرداد','تیر','مرداد')
    reports=('گزارش','آمار','جمع','پرداخت','واریز','دریافت','کمک','مانده','هزینه','درآمد')
    if any(t in q for t in periods) and any(t in q for t in reports):
        return True
    if any(t in q for t in ('چقدر','چند','جمع','مانده','ریز','تعداد')) and any(t in q for t in ('واریز','پرداخت','کرون','تومان','ریال','یورو','کمک','صدقه','پول','هزینه')):
        return True
    return False

def category(text):
    for name,terms in (
        ('کمک و پرداخت',('کمک','واریز','کرون','تومان','رسید','پول','صدقه')),
        ('نیاز و درخواست',('نیاز','خانواده','بیمار','نیازمند','فقر','کمبود')),
        ('مراسم و برنامه',('جشن','مراسم','برنامه','هیئت','میلاد','فاطمیه','اطعام')),
        ('پیگیری',('پیگیری','منتظر','هماهنگ','انجام شد','تکمیل','ارسال'))):
        if any(t in text for t in terms):
            return name
    return 'سایر'

class RecentContext:
    def __init__(self, limit=120, max_age=48*3600):
        self.limit=limit
        self.max_age=max_age
        self.items=OrderedDict()
        self.baseline=[]
        self.lock=RLock()

    def add(self, chat, msg, now=None):
        if not isinstance(msg,dict):return
        try: key=(int(chat),int(msg['message_id']))
        except (ValueError,TypeError,KeyError):return
        author=msg.get('sender_chat') or msg.get('from') or {}
        if not isinstance(author,dict) or author.get('is_bot'):return
        raw=msg.get('text') or msg.get('caption') or ''
        if not isinstance(raw,str):return
        content=raw.strip()
        if not content:
            kind=next((k for k in ('voice','audio','photo','video','document') if msg.get(k)),None)
            if not kind:return
            content='[پیوست '+kind+'؛ محتوای تأییدشده در دسترس نیست]'
        now=int(time.time() if now is None else now)
        try:stamp=int(msg.get('date',now))
        except (TypeError,ValueError):stamp=now
        if stamp<now-self.max_age:return
        author_name=str(author.get('username') or author.get('first_name') or author.get('id') or 'نامعلوم')[:80]
        item={'id':key[1],'date':stamp,'sender':author_name,'text':content[:1200],
              'category_hint':category(content)}
        with self.lock:
            self.items.pop(key,None)
            self.items[key]=item
            self._prune(now)

    def seed(self, chat, rows, now=None):
        """Memory-only snapshot of previously approved durable sender messages.

        This is invoked once on start, not for every user question.
        Other group members' messages never enter the persistent baseline.
        """
        try: cid=int(chat)
        except (ValueError,TypeError):return 0
        now=int(time.time() if now is None else now)
        initial=[]
        for data in rows:
            try:
                mid=int(data['id']);stamp=int(data['date'])
            except (KeyError,TypeError,ValueError):
                continue
            raw=data.get('text')
            if not isinstance(raw,str) or not raw.strip():
                continue
            if not now-60*24*3600<=stamp<=now+60:
                continue
            text=raw.strip()[:1200]
            initial.append({'id':mid,'date':stamp,'sender':'@motahari58',
                            'text':text,'category_hint':category(text),
                            'source':'verified_archive_seed'})
        with self.lock:
            self.baseline=[(cid,row) for row in initial[-60:]]
        return len(initial[-60:])

    def _prune(self,now):
        for key,item in list(self.items.items()):
            if item['date']<now-self.max_age:self.items.pop(key,None)
        while len(self.items)>self.limit:self.items.popitem(last=False)

    def snapshot(self,chat,limit=28,max_chars=8500,now=None):
        try:cid=int(chat)
        except (TypeError,ValueError):return {'messages':[],'category_hints':{},'complete':False}
        now=int(time.time() if now is None else now)
        with self.lock:
            self._prune(now)
            live=[dict(row) for (c,_),row in self.items.items() if c==cid]
            saved=[dict(row) for c,row in self.baseline if c==cid]
        # Fresh in-memory messages override duplicate startup records.
        by_id={row['id']:row for row in saved}
        by_id.update({row['id']:row for row in live})
        rows=sorted(by_id.values(),key=lambda r:(r['date'],r['id']))
        chosen=[];size=0
        for row in reversed(rows):
            length=len(row['text'])+len(row['sender'])+80
            if size+length>max_chars:continue
            size+=length;chosen.append(row)
            if len(chosen)>=limit:break
        chosen.reverse()
        tags={}
        for r in chosen:tags[r['category_hint']]=tags.get(r['category_hint'],0)+1
        return {'messages':chosen,'category_hints':tags,'complete':False,
                'startup_archive_items':len(saved),
                'live_items':len(live),
                'note':('پیام‌های زنده محدود به موارد دریافت‌شده پس از راه‌اندازی است. '
                        'موارد با source=verified_archive_seed از آرشیو مجاز @motahari58 '
                        'یک بار هنگام شروع بازیابی شده‌اند؛ ممکن است چند هفته قدیمی باشند. '
                        'پیام‌های سایر اعضا فقط در حافظهٔ زنده دیده می‌شوند. '
                        'اگر تعداد live_items صفر است، دربارهٔ پیام‌های همهٔ گروه ادعای جامع نکن.')}
