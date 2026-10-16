"""Hamdel's durable Bale archive. No secrets or messages are logged."""
import csv
import hashlib
import hmac
import io
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import urllib.request
import urllib.error
import zipfile
from hamdel.config import settings as runtime_settings
from hamdel.live_context import RecentContext, needs_history
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(os.getenv('DATA_DIR', 'data'))
TOKEN = os.getenv('BALE_BOT_TOKEN', '')
MAX_FILE = 20 * 1024 * 1024
API = 'https://tapi.bale.ai'
ARCHIVE_SENDER = str(runtime_settings().get('archive_sender_username','motahari58')).strip().lstrip('@').casefold()
STATE = {'state': 'starting', 'last_poll': 0, 'speech_state': 'checking'}
LIVE_GROUP = RecentContext()
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')

def db():
    c = sqlite3.connect(ROOT / 'archive.sqlite3', timeout=30)
    c.row_factory = sqlite3.Row
    return c

def init():
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / 'media').mkdir(exist_ok=True)
    with db() as c:
        c.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS ai_jobs(id INTEGER PRIMARY KEY, chat INTEGER, prompt TEXT,
            response TEXT DEFAULT '', status TEXT DEFAULT 'pending', created INTEGER);
        CREATE TABLE IF NOT EXISTS speech_jobs(chat INTEGER, id INTEGER, file_id TEXT, filename TEXT,
            size INTEGER, duration INTEGER, caption TEXT, reply INTEGER, status TEXT DEFAULT 'pending',
            transcript TEXT DEFAULT '', PRIMARY KEY(chat,id));
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS archive_identities(username TEXT, kind TEXT, sender_id INTEGER,
            PRIMARY KEY(username,kind,sender_id));
        CREATE TABLE IF NOT EXISTS media_insights(file_id TEXT PRIMARY KEY, version TEXT,
            state TEXT, summary TEXT DEFAULT '', scope TEXT DEFAULT '', digest TEXT DEFAULT '',
            attempts INTEGER DEFAULT 0, retry_at INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS updates(id INTEGER PRIMARY KEY, payload TEXT, received INTEGER);
        CREATE TABLE IF NOT EXISTS messages(chat INTEGER, id INTEGER, date INTEGER, text TEXT,
            kind TEXT, file_id TEXT, filename TEXT, size INTEGER, media_group TEXT,
            status TEXT, path TEXT, attempts INTEGER DEFAULT 0, retry_at INTEGER DEFAULT 0,
            PRIMARY KEY(chat,id));
        ''')
        # Group voice messages are archived but must not be transmitted to third-party ASR by default.
        c.execute("UPDATE speech_jobs SET status='archived_only' WHERE reply=0 AND status IN ('pending','processing')")
        columns={r[1] for r in c.execute('PRAGMA table_info(messages)')}
        for name, dtype in (('source_username','TEXT'),('source_id','INTEGER'),('source_kind','TEXT')):
            if name not in columns:c.execute(f'ALTER TABLE messages ADD COLUMN {name} {dtype}')
        c.execute('CREATE INDEX IF NOT EXISTS idx_archive_source ON messages(chat,source_username,date,id)')
        columns={r[1] for r in c.execute('PRAGMA table_info(ai_jobs)')}
        if 'artifact' not in columns: c.execute("ALTER TABLE ai_jobs ADD COLUMN artifact TEXT DEFAULT ''")
        columns={r[1] for r in c.execute('PRAGMA table_info(speech_jobs)')}
        if 'created' not in columns:c.execute("ALTER TABLE speech_jobs ADD COLUMN created INTEGER DEFAULT 0")

def setting(key, default=''):
    with db() as c:
        r = c.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    return r[0] if r else default

def set_setting(key, value):
    with db() as c:
        c.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (key, str(value)))

def api(method, data=None):
    req = urllib.request.Request(f'{API}/bot{TOKEN}/{method}',
        data=json.dumps(data or {}).encode(), headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req, timeout=55) as r:
        result = json.load(r)
    if not result.get('ok'):
        raise RuntimeError('Bale API rejected ' + method)
    return result['result']

def send(chat, text):
    api('sendMessage', {'chat_id':chat, 'text':text})

def admin(chat, uid):
    return any(x.get('user',{}).get('id') == uid for x in api('getChatAdministrators', {'chat_id':chat}))


def archive_author(m):
    """Actual author only, not a forwarded-from or quoted user."""
    sender_chat=m.get('sender_chat')
    kind='chat' if isinstance(sender_chat,dict) and sender_chat else 'user'
    sender=sender_chat if kind=='chat' else m.get('from')
    if not isinstance(sender,dict):return None
    username=str(sender.get('username') or '').strip().lstrip('@').casefold()
    try:
        sid=int(sender.get('id'))
        if sid==0:sid=None
    except (TypeError,ValueError):sid=None
    if username==ARCHIVE_SENDER:
        if sid is not None:
            with db() as c:
                c.execute('INSERT OR IGNORE INTO archive_identities VALUES(?,?,?)',
                          (ARCHIVE_SENDER,kind,sid))
        return ARCHIVE_SENDER,sid,kind
    if sid is not None:
        with db() as c:
            known=c.execute('SELECT 1 FROM archive_identities WHERE username=? AND kind=? AND sender_id=?',
                            (ARCHIVE_SENDER,kind,sid)).fetchone()
        if known:return ARCHIVE_SENDER,sid,kind
    return None


def export(chat):
    with db() as c:
        rows = [dict(r) for r in c.execute("SELECT m.*,i.summary AS media_summary,i.scope AS media_scope,i.state AS media_state FROM messages m LEFT JOIN media_insights i ON m.file_id=i.file_id AND i.version='media-v1' WHERE m.chat=? AND m.source_username=? ORDER BY m.date,m.id", (chat,ARCHIVE_SENDER))]
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('messages.json', json.dumps(rows, ensure_ascii=False, indent=2))
        s = io.StringIO(); w = csv.writer(s)
        w.writerow(['date_utc','message_id','text','type','file_status','filename','stored_path'])
        report = ['# آرشیو گزارش‌های همدل', '', 'مبالغ به صورت خودکار جمع نشده‌اند؛ موارد تکراری باید بررسی شوند.', '']
        for r in rows:
            stamp = time.strftime('%Y-%m-%d %H:%M UTC',time.gmtime(r['date']))
            text = r['text'] or ''
            safe_text = "'"+text if text.startswith(('=','+','-','@','\t','\r')) else text
            w.writerow([stamp,r['id'],safe_text,r['kind'],r['status'],r['filename'],r['path']])
            report += [f'## {stamp} — {r["id"]}',text,f'فایل: {r["filename"] or "—"} | وضعیت: {r["status"]}',r['media_summary'] or '',r['media_scope'] or '','']
        z.writestr('messages.csv', '\ufeff'+s.getvalue())
        z.writestr('report-source.md','\n'.join(report))
    content=out.getvalue()
    if len(content)>MAX_FILE:
        send(chat,'حجم خروجی زیاد است؛ خروجی را از دیسک لیارا دریافت کنید.'); return
    boundary='hamdel_'+os.urandom(12).hex()
    head=(f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{chat}\r\n'
          f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="hamdel-reports.zip"\r\nContent-Type: application/zip\r\n\r\n').encode()
    body=head+content+f'\r\n--{boundary}--\r\n'.encode()
    req=urllib.request.Request(f'{API}/bot{TOKEN}/sendDocument', data=body,
        headers={'Content-Type':f'multipart/form-data; boundary={boundary}'})
    with urllib.request.urlopen(req,timeout=90) as resp:
        if not json.load(resp).get('ok'): raise RuntimeError('export failed')

def attachment(m):
    doc=m.get('document',{})
    if doc and (Path(doc.get('file_name','')).suffix.lower() in ('.opus','.ogg','.mp3','.m4a','.wav') or doc.get('mime_type','').startswith('audio/')):return 'audio',doc
    if m.get('photo'):
        return 'photo', max(m['photo'], key=lambda p:p.get('file_size',p.get('width',0)*p.get('height',0)))
    for k in ('video','document','audio','voice','animation','video_note','sticker'):
        if m.get(k): return k,m[k]
    return 'text',{}


AI_BINARY = ROOT / 'codex-runtime/package/vendor/x86_64-unknown-linux-musl/bin/codex'
AI_AUTH = ROOT / 'hamdel-codex-auth'

def enqueue_ai(mid, cid, text):
    with db() as c:
        c.execute('INSERT OR IGNORE INTO ai_jobs(id,chat,prompt,created) VALUES(?,?,?,?)',
                  (mid,cid,text[:8000],int(time.time())))

def answer_ai(cid, question, jobid):
    import agent_bridge
    recent=LIVE_GROUP.snapshot(setting('chat_id'))
    if not needs_history(question):
        # No archive reads on normal questions. Codex resumes its own chat session.
        context={'mode':'conversation','group_context':recent,
                 'archive_index':[],'selected_reports':[],
                 'total_archived':None,'included':0,'archive_coverage':{},
                 'today_utc':time.strftime('%Y-%m-%d',time.gmtime())}
        return agent_bridge.reply(cid,question,context,jobid)
    with db() as c:
        rows=[dict(r) for r in c.execute(
            "SELECT m.id,m.date,m.text,m.kind,m.status,m.filename,i.summary AS media_summary,i.scope AS media_scope,i.state AS media_state,i.digest AS content_hash FROM messages m LEFT JOIN media_insights i ON m.file_id=i.file_id AND i.version='media-v1' WHERE m.chat=? AND m.source_username=? ORDER BY m.date DESC,m.id DESC LIMIT 1000",
            (int(setting('chat_id')),ARCHIVE_SENDER))]
        history=[dict(r) for r in c.execute(
            "SELECT prompt,response FROM ai_jobs WHERE chat=? AND status='done' ORDER BY created DESC,id DESC LIMIT 4",(cid,))]
        total=c.execute('SELECT count(*) FROM messages WHERE chat=? AND source_username=?',(int(setting('chat_id')),ARCHIVE_SENDER)).fetchone()[0]
        coverage=dict(c.execute("SELECT COALESCE(i.state,CASE WHEN m.status='text' THEN 'text' WHEN m.status='saved' THEN 'pending_analysis' ELSE m.status END),count(*) FROM messages m LEFT JOIN media_insights i ON m.file_id=i.file_id AND i.version='media-v1' WHERE m.chat=? AND m.source_username=? GROUP BY 1",(int(setting('chat_id')),ARCHIVE_SENDER)))
    archive_index=[]
    for source in rows:
        if source['kind'] in ('voice','audio') or Path(source.get('filename') or '').suffix.lower() in ('.opus','.ogg','.mp3','.m4a','.wav'):
            # Legacy automatic transcripts were reported as unintelligible. They are
            # not evidence, even if an old media cache marked the file as done.
            source['text']=(source['text'] or '').split('[متن خودکار صوت')[0].strip()
            source['media_summary']=''
            source['media_state']='unverified_audio'
            source['media_scope']='Original audio archived; no owner-verified transcript available'
        source=dict(source)
        source['date_utc']=time.strftime('%Y-%m-%d %H:%M',time.gmtime(source.pop('date')))
        source['text']=(source['text'] or '')[:3500]
        source['media_summary']=(source['media_summary'] or '')[:2200]
        archive_index.append(source)
    for h in history:
        h['prompt']=h['prompt'][:2500];h['response']=h['response'][:3500]
    # Local ranking and hard budgets avoid sending the entire archive on every turn.
    terms={w for w in re.findall(r'[^\W_]+',question) if len(w)>3}
    rows=sorted(enumerate(rows),key=lambda pair: (sum(t in ((pair[1]['text'] or '')+' '+(pair[1]['media_summary'] or '')) for t in terms),-pair[0]),reverse=True)
    greeting=question.strip(' !؟.').lower() in ('سلام','سلام خوبی','خوبی','ممنون','مرسی','hi','hello')
    budget=0 if greeting else 24000;selected=[]
    for _,row in rows:
        row['text']=(row['text'] or '')[:3500]
        row['media_summary']=(row['media_summary'] or '')[:2200]
        row['media_state']=row['media_state'] or ('pending_analysis' if row['status']=='saved' else row['status'])
        row['date_utc']=time.strftime('%Y-%m-%d %H:%M',time.gmtime(row.pop('date')))
        size=len(json.dumps(row,ensure_ascii=False))
        if size>budget: continue
        budget-=size;selected.append(row)
        if len(selected)>=30:break
    selected.sort(key=lambda r:(r['date_utc'],r['id']))
    context={'mode':'historical_report','group_context':recent,
             'total_archived':total,'included':len(selected),'archive_coverage':coverage,
             'selected_reports':selected, 'archive_index':archive_index, 'conversation':list(reversed(history)),
             'question':question,'today_utc':time.strftime('%Y-%m-%d',time.gmtime())}
    import agent_bridge
    return agent_bridge.reply(cid,question,context,jobid)

def process_media():
    """One idle-time analysis; same worker as speech/chat to bound memory."""
    import media_reader
    with db() as c:
        row=c.execute("""SELECT m.* FROM messages m LEFT JOIN media_insights i ON m.file_id=i.file_id
            WHERE m.chat=? AND m.source_username=? AND m.status='saved' AND m.kind NOT IN ('voice','audio','sticker')
            AND (i.file_id IS NULL OR i.version!=? OR (i.state='failed' AND i.attempts<3 AND i.retry_at<?))
            ORDER BY CASE WHEN m.kind='photo' THEN 0 WHEN m.kind='video' THEN 1 ELSE 2 END,m.date,m.id LIMIT 1""",
            (int(setting('chat_id') or 0),ARCHIVE_SENDER,media_reader.VERSION,int(time.time()))).fetchone()
    if not row:return False
    state='done';summary='';scope='';digest=''
    try:
        path=(ROOT/row['path']).resolve()
        if not path.is_relative_to((ROOT/'media').resolve()):raise ValueError('invalid_path')
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        with db() as c:
            cached=c.execute("SELECT summary,scope FROM media_insights WHERE digest=? AND version=? AND state='done' LIMIT 1",(digest,media_reader.VERSION)).fetchone()
        if cached:summary,scope=cached
        else:scope,summary=media_reader.analyze(path,row['kind'],row['filename'])
    except media_reader.Unsupported:
        state='unsupported';scope='This file format or size could not be analyzed.'
    except Exception:
        state='failed';scope='Analysis failed; content has NOT been read.'
        logging.warning('Media analysis incomplete; no content logged')
    with db() as c:
        c.execute("""INSERT INTO media_insights(file_id,version,state,summary,scope,digest,attempts,retry_at)
            VALUES(?,?,?,?,?,?,1,?) ON CONFLICT(file_id) DO UPDATE SET version=excluded.version,
            state=excluded.state,summary=excluded.summary,scope=excluded.scope,digest=excluded.digest,
            attempts=media_insights.attempts+1,retry_at=excluded.retry_at""",
            (row['file_id'],media_reader.VERSION,state,summary,scope,digest,int(time.time())+600))
    logging.info('Media analysis status: %s',state)
    return True

def send_image(chat,path):
    content=Path(path).read_bytes()
    if len(content)>MAX_FILE: raise TooLarge()
    boundary='hamdel_'+os.urandom(12).hex()
    head=(f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{chat}\r\n'
          f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="hamdel-poster.png"\r\nContent-Type: image/png\r\n\r\n').encode()
    body=head+content+f'\r\n--{boundary}--\r\n'.encode()
    req=urllib.request.Request(f'{API}/bot{TOKEN}/sendPhoto',data=body,
        headers={'Content-Type':f'multipart/form-data; boundary={boundary}'})
    with urllib.request.urlopen(req,timeout=90) as r:
        if not json.load(r).get('ok'):raise RuntimeError('photo delivery failed')

def send_document(chat,path):
    content=Path(path).read_bytes()
    if len(content)>MAX_FILE: raise TooLarge()
    boundary='hamdel_'+os.urandom(12).hex()
    head=(f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{chat}\r\n'
          f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="hamdel-report{Path(path).suffix}"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()
    body=head+content+f'\r\n--{boundary}--\r\n'.encode()
    req=urllib.request.Request(f'{API}/bot{TOKEN}/sendDocument',data=body,
        headers={'Content-Type':f'multipart/form-data; boundary={boundary}'})
    with urllib.request.urlopen(req,timeout=90) as r:
        if not json.load(r).get('ok'):raise RuntimeError('photo delivery failed')

def enqueue_speech(m, reply):
    kind,f=attachment(m)
    if kind not in ('voice','audio'):return
    with db() as c:
        c.execute('INSERT OR IGNORE INTO speech_jobs(chat,id,file_id,filename,size,duration,caption,reply,created) VALUES(?,?,?,?,?,?,?,?,?)',
            (m['chat']['id'],m['message_id'],f['file_id'],f.get('file_name') or str(m['message_id'])+'.ogg',
             f.get('file_size',0),f.get('duration',0),m.get('caption',''),int(reply),int(time.time())))

def process_speech():
    import multimodal
    with db() as c:
        row=c.execute("SELECT * FROM speech_jobs WHERE status='pending' AND reply=1 ORDER BY created,id LIMIT 1").fetchone()
        if not row:return False
        media=c.execute('SELECT status FROM messages WHERE chat=? AND id=?',(row['chat'],row['id'])).fetchone()
        if media and media['status']=='pending':return False
        c.execute("UPDATE speech_jobs SET status='processing' WHERE chat=? AND id=?",(row['chat'],row['id']))
    try:
        if row['size']>MAX_FILE or row['duration']>600:raise TooLarge()
        with db() as c:
            stored=c.execute("SELECT path FROM messages WHERE chat=? AND id=? AND status='saved'",(row['chat'],row['id'])).fetchone()
        if stored and stored['path']:path=stored['path']
        else:
            path,_=download_one(row)
        transcript, alternative=multimodal.transcribe(ROOT/path,with_alternatives=True)
        suggestion=transcript
        try:
            from hamdel.speech import polish_transcript_with_codex
            suggestion=polish_transcript_with_codex(transcript,ROOT,alternative=alternative)
        except Exception:
            logging.warning('Codex speech proofreading unavailable; original preserved')
        note='[متن خودکار صوت؛ اسم‌ها و مبلغ‌ها نیازمند بازبینی هستند]\n'+transcript
        with db() as c:
            c.execute("UPDATE speech_jobs SET status='done',transcript=? WHERE chat=? AND id=?",(transcript,row['chat'],row['id']))
            c.execute('UPDATE messages SET text=? WHERE chat=? AND id=?',(row['caption']+'\n'+note,row['chat'],row['id']))
        if row['reply']:
            import agent_bridge
            # Keep the actual transcript separate from explanatory labels.
            caption=(row['caption'] or '').strip()
            plain=(caption+'\n'+transcript).strip() if caption else transcript
            polished=(caption+'\n'+suggestion).strip() if caption else suggestion
            agent_bridge.voice_ready(row['chat'],row['id'],plain,
                                      suggested=polished if polished != plain else None)
    except Exception as e:
        with db() as c:c.execute("UPDATE speech_jobs SET status='failed' WHERE chat=? AND id=?",(row['chat'],row['id']))
        logging.warning('Speech processing failed: %s',type(e).__name__)
        if row['reply']:
            from hamdel.speech import SpeechUnavailable
            if isinstance(e,SpeechUnavailable):send(row['chat'],str(e));return True
            send(row['chat'],'ویس باید حداکثر ۱۰ دقیقه و ۲۰ مگابایت باشد.' if isinstance(e,TooLarge) else 'تبدیل این صوت کامل نشد. لطفاً یک ویس کوتاه‌تر و واضح‌تر بفرست.')
    return True

def ai_worker():
    # A stopped generation can safely restart; an uncertain send is not repeated.
    with db() as c:
        c.execute("UPDATE ai_jobs SET status='pending' WHERE status='generating'")
        c.execute("UPDATE speech_jobs SET status='pending' WHERE status='processing'")
    while True:
        try:
            with db() as c:
                row=c.execute("SELECT * FROM ai_jobs WHERE status IN ('pending','ready') ORDER BY created,id LIMIT 1").fetchone()
                voice=c.execute("SELECT created FROM speech_jobs WHERE status='pending' AND reply=1 ORDER BY created,id LIMIT 1").fetchone()
                voice_first=voice and (not row or voice['created']<=row['created'])
                if row and not voice_first and row['status']=='pending':
                    c.execute("UPDATE ai_jobs SET status='generating' WHERE id=?",(row['id'],))
            if not row or voice_first:
                if not process_speech() and not process_media(): time.sleep(1)
                continue
            response=row['response'];artifact=row['artifact']
            if row['status']=='pending':
                try: response,artifact=answer_ai(row['chat'],row['prompt'],row['id'])
                except Exception:
                    logging.warning('AI generation unavailable; no content logged')
                    response='الان نتوانستم از هوش مصنوعی پاسخ بگیرم؛ لطفاً کمی بعد دوباره بپرس. آرشیو گزارش‌ها همچنان فعال است.'
                with db() as c: c.execute("UPDATE ai_jobs SET response=?,artifact=?,status='ready' WHERE id=?",(response,artifact,row['id']))
            with db() as c: c.execute("UPDATE ai_jobs SET status='sending' WHERE id=?",(row['id'],))
            for offset in range(0,len(response),3000): send(row['chat'],response[offset:offset+3000])
            if artifact: send_image(row['chat'],artifact)
            import agent_bridge
            buttons=agent_bridge.keyboard(row['chat'],row['id'])
            for document in agent_bridge.artifacts(row['chat'],row['id']):send_document(row['chat'],document)
            if buttons: api('sendMessage',{'chat_id':row['chat'],'text':'این پیش‌نویس را ثبت کنیم؟','reply_markup':buttons})
            with db() as c: c.execute("UPDATE ai_jobs SET status='done' WHERE id=?",(row['id'],))
        except Exception:
            logging.warning('AI delivery needs review; no content logged'); time.sleep(5)

def handle(u):
    if u.get('callback_query'):
        import agent_bridge
        agent_bridge.callback(u['callback_query'],u['update_id']);return
    mid=u['update_id']; m=u.get('message') or u.get('edited_message')
    if not m: return
    chat=m.get('chat',{}); cid=chat.get('id'); uid=m.get('from',{}).get('id')
    text=m.get('text') or m.get('caption') or ''
    bound=setting('chat_id')
    command=text.split(maxsplit=1)[0].split('@')[0] if text else ''
    if not bound:
        if command != '/connect' or chat.get('type') not in ('group','supergroup'): return
        parts=text.split(maxsplit=1)
        expected=setting('pair_code')
        if len(parts)!=2 or not hmac.compare_digest(parts[1].strip(),expected): return
        if not admin(cid,uid): return
        set_setting('chat_id',cid); set_setting('owner_id',uid); set_setting('pair_code','')
        send(cid,'✅ گروه همدل متصل شد. فقط پیام‌های @motahari58 و فایل‌هایشان تا ۲۰ مگابایت آرشیو می‌شوند.\n/status وضعیت آرشیو\n/export خروجی متن و فهرست مستندات (برای مدیران)')
        logging.info('Group pairing complete')
        return
    owner = str(uid)==setting('owner_id')
    if chat.get('type')=='private':
        if not owner or u.get('edited_message'): return
        if attachment(m)[0] in ('voice','audio'):
            enqueue_speech(m,True);return
        if m.get('photo') or m.get('document'):
            enqueue_ai(mid,cid,'__HAMDEL_MEDIA__'+json.dumps(m,ensure_ascii=False));return
        if not text:return
        if command in ('/start','/help'):
            enqueue_ai(mid,cid,'سلام، خیلی کوتاه توضیح بده چطور می‌توانم درباره گزارش‌های همدل با تو صحبت کنم.')
        else: enqueue_ai(mid,cid,text)
        return
    if str(cid)!=bound: return
    # Recent messages of all group members are retained only in RAM.
    # The permanent archive below stays restricted to @motahari58.
    LIVE_GROUP.add(cid,m)
    addressed = '@hamdel_report_bot' in text.lower() or m.get('reply_to_message',{}).get('from',{}).get('username','').lower()=='hamdel_report_bot'
    if owner and addressed and (m.get('voice') or m.get('audio')) and not u.get('edited_message'):
        enqueue_speech(m,True)
    if owner and addressed and text and not (m.get('voice') or m.get('audio')) and not u.get('edited_message'):
        enqueue_ai(mid,cid,text)
        return
    if command in ('/status','/export','/help'):
        with db() as c:
            if c.execute('SELECT 1 FROM updates WHERE id=?',(mid,)).fetchone(): return
        if not admin(cid,uid): return
        if command=='/export': export(cid)
        elif command=='/status':
            with db() as c:
                total=c.execute('SELECT count(*) FROM messages WHERE chat=? AND source_username=?',(cid,ARCHIVE_SENDER)).fetchone()[0]
                states=dict(c.execute('SELECT status,count(*) FROM messages WHERE chat=? AND source_username=? GROUP BY status',(cid,ARCHIVE_SENDER)))
            send(cid,f'گزارش‌های ثبت‌شده: {total}\nفایل ذخیره‌شده: {states.get("saved",0)}\nبزرگ‌تر از سقف: {states.get("too_large",0)}\nدر انتظار/نیازمند بررسی: {states.get("pending",0)+states.get("failed",0)}\nفضای خالی: {shutil.disk_usage(ROOT).free//(1024*1024)} مگابایت')
        else: send(cid,'فقط پیام‌های @motahari58 در گروه آرشیو می‌شوند. /export خروجی متن و فهرست فایل‌هاست. اصل فایل‌ها روی دیسک archive لیارا قرار دارند. گفت‌وگوی هوشمند برای مالک متصل‌کننده در خصوصی فعال است؛ در گروه ربات را منشن کنید یا به پیامش پاسخ دهید. بدون کامند صحبت کنید، ویس بفرستید یا درخواست کمک و پوستر بخواهید. ویس تا ۱۰ دقیقه و ۲۰ مگابایت؛ متن صوت و مبلغ‌ها را قبل از انتشار بررسی کنید.')
        with db() as c: c.execute('INSERT OR IGNORE INTO updates VALUES(?,?,?)',(mid,'{"command":true}',int(time.time())))
        return
    author=archive_author(m)
    if author is None:return
    kind,f=attachment(m)
    size=f.get('file_size',0) or 0
    file_id=f.get('file_id','')
    name=f.get('file_name') or (str(m['message_id'])+{'photo':'.jpg','video':'.mp4','voice':'.ogg'}.get(kind,'.bin') if f else '')
    status='too_large' if size>MAX_FILE else ('pending' if file_id else 'text')
    with db() as c:
        if c.execute('SELECT 1 FROM updates WHERE id=?',(mid,)).fetchone(): return
        old=c.execute('SELECT * FROM messages WHERE chat=? AND id=?',(cid,m['message_id'])).fetchone()
        path=''
        if old and old['file_id']==file_id:
            status=old['status']; path=old['path']
        c.execute('INSERT OR REPLACE INTO messages(chat,id,date,text,kind,file_id,filename,size,media_group,status,path,source_username,source_id,source_kind) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (cid,m['message_id'],m.get('date',int(time.time())),text,kind,file_id,name,size,m.get('media_group_id',''),status,path,*author))
        c.execute('INSERT INTO updates VALUES(?,?,?)',(mid,'{"archived":true}',int(time.time())))
    # Non-addressed group voice is archived only; it is never sent to Cloudflare.

class TooLarge(Exception): pass

def download_one(row):
    f=api('getFile',{'file_id':row['file_id']})
    if (f.get('file_size') or 0)>MAX_FILE: raise TooLarge()
    remote=f['file_path']
    if remote.startswith(('http:','https:')) or '..' in remote.split('/'):
        raise RuntimeError('unexpected file path')
    digest=hashlib.sha256(row['file_id'].encode()).hexdigest()[:20]
    suffix=Path(row['filename']).suffix[:12]
    if not re.fullmatch(r'\.[a-zA-Z0-9]+',suffix): suffix='.bin'
    dest=ROOT/'media'/f'{digest}{suffix}'; temp=dest.with_suffix('.part')
    try:
        if shutil.disk_usage(ROOT).free < MAX_FILE+64*1024*1024: raise RuntimeError('storage full')
        with urllib.request.urlopen(f'{API}/file/bot{TOKEN}/{remote}',timeout=90) as r, open(temp,'wb') as out:
            count=0
            while True:
                chunk=r.read(256*1024)
                if not chunk: break
                count+=len(chunk)
                if count>MAX_FILE: raise TooLarge()
                out.write(chunk)
            if count==0: raise RuntimeError('empty download')
            out.flush(); os.fsync(out.fileno())
        os.replace(temp,dest)
        return str(dest.relative_to(ROOT)),count
    finally:
        temp.unlink(missing_ok=True)

def media_worker():
    while True:
        try:
            with db() as c:
                row=c.execute("SELECT * FROM messages WHERE source_username=? AND status='pending' AND retry_at<=? ORDER BY date LIMIT 1",(ARCHIVE_SENDER,int(time.time()))).fetchone()
            if not row: time.sleep(2); continue
            try:
                path,size=download_one(row)
                with db() as c: c.execute("UPDATE messages SET status='saved',path=?,size=? WHERE chat=? AND id=? AND file_id=?",(path,size,row['chat'],row['id'],row['file_id']))
            except TooLarge:
                with db() as c: c.execute("UPDATE messages SET status='too_large' WHERE chat=? AND id=?",(row['chat'],row['id']))
            except Exception:
                attempts=row['attempts']+1
                with db() as c: c.execute('UPDATE messages SET status=?,attempts=?,retry_at=? WHERE chat=? AND id=?',('failed' if attempts>=10 else 'pending',attempts,int(time.time())+min(3600,30*2**attempts),row['chat'],row['id']))
                logging.warning('Media download deferred; no credential details logged')
        except Exception:
            logging.warning('Archive media worker retrying'); time.sleep(10)

def poll():
    if not TOKEN:
        STATE['state']='missing_token'; logging.error('BALE_BOT_TOKEN is missing'); return
    while True:
        try:
            me=api('getMe')
            if me.get('username','').lower()!='hamdel_report_bot':
                STATE['state']='unexpected_bot'; logging.error('Bot identity does not match hamdel_report_bot'); return
            info=api('getWebhookInfo')
            if info.get('url'):
                STATE['state']='webhook_conflict'; logging.error('Existing webhook must be reviewed before polling'); return
            logging.info('Bale bot identity verified')
            break
        except Exception:
            STATE['state']='api_unreachable'; logging.warning('Bale connection retrying'); time.sleep(15)
    if not setting('chat_id') and not setting('pair_code'):
        import secrets
        set_setting('pair_code',secrets.token_hex(8))
    if not setting('chat_id'):
        logging.info('PAIR_GROUP_COMMAND /connect %s',setting('pair_code'))
    while True:
        try:
            updates=api('getUpdates',{'offset':int(setting('offset','0')),'limit':50,'timeout':25})
            for u in updates:
                handle(u)
                set_setting('offset',u['update_id']+1)
            STATE.update(state='running' if setting('chat_id') else 'awaiting_group',last_poll=int(time.time()))
        except Exception:
            STATE['state']='retrying'; logging.warning('Polling retry; details suppressed'); time.sleep(10)

def probe_speech_readiness():
    """Prove the running process can call Workers AI; never send user recordings."""
    from hamdel.config import settings
    if settings().get('speech_provider') != 'cloudflare_free':
        STATE['speech_state'] = 'disabled'
        return
    if not os.environ.get('CLOUDFLARE_ACCOUNT_ID') or not os.environ.get('CLOUDFLARE_API_TOKEN'):
        STATE['speech_state'] = 'missing_credentials'
        return
    try:
        from contextlib import redirect_stdout
        from io import StringIO
        from scripts.check_cloudflare_speech import main as check_cloudflare
        output = StringIO()
        with redirect_stdout(output):
            success = check_cloudflare() == 0
        STATE['speech_state'] = 'ready' if success else 'api_error'
        if not success:
            details = output.getvalue()
            code = re.search(r'Cloudflare: HTTP ([0-9]{3})', details)
            token_error = re.search(r'Cloudflare token verification HTTP ([0-9]{3})', details)
            if code:
                STATE['speech_issue'] = 'workers_ai_http_' + code.group(1)
            elif token_error:
                STATE['speech_issue'] = 'token_verify_http_' + token_error.group(1)
            elif 'connectivity failure' in details or 'verification unavailable' in details:
                STATE['speech_issue'] = 'network'
            else:
                STATE['speech_issue'] = 'unknown'
        else:
            STATE['speech_issue'] = 'none'
    except Exception as exc:
        STATE['speech_state'] = 'api_error'
        STATE['speech_issue'] = 'runtime_' + type(exc).__name__
        logging.warning('Speech readiness probe failed; no credentials logged')


class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ('/','/health'):
            self.send_error(404); return
        release=json.loads(Path('release.json').read_text()) if Path('release.json').exists() else {}
        data=json.dumps({'release':release.get('commit','local'),'service':'hamdel-reporter','version':'agent-v3-github',**STATE}).encode()
        self.send_response(200); self.send_header('Content-Type','application/json'); self.end_headers(); self.wfile.write(data)
    def log_message(self,*args): pass

if __name__=='__main__':
    import sys
    sys.modules['main']=sys.modules[__name__]
    init()
    threading.Thread(target=probe_speech_readiness,daemon=True).start()
    threading.Thread(target=media_worker,daemon=True).start()
    threading.Thread(target=ai_worker,daemon=True).start()
    threading.Thread(target=poll,daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0',int(os.getenv('PORT','8080'))),Health).serve_forever()
