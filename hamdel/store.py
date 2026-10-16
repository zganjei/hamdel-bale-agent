"""Trusted storage and approval boundary. Agents cannot commit donations or publish."""
from __future__ import annotations
import csv
import hashlib
import io
import json
import sqlite3
import time
import uuid
from collections import defaultdict
from datetime import date
from pathlib import Path
from contextlib import contextmanager

CURRENCIES = {"SEK", "IRT", "IRR", "EUR"}  # IRT = Iranian toman, NOT rial
KINDS = {"content", "aid_request", "report", "donation", "poster_brief"}


def validate(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("پیش‌نویس باید یک شیء JSON باشد.")
    kind = payload.get("kind")
    if kind not in KINDS:
        raise ValueError("نوع پیش‌نویس پشتیبانی نمی‌شود.")
    title, body = payload.get("title"), payload.get("body")
    if not isinstance(title, str) or not (1 <= len(title.strip()) <= 120):
        raise ValueError("عنوان پیش‌نویس معتبر نیست.")
    if not isinstance(body, str) or len(body) > 15000:
        raise ValueError("متن پیش‌نویس معتبر نیست.")
    data = payload.get("data", {})
    if not isinstance(data, dict):
        raise ValueError("دادهٔ پیش‌نویس نامعتبر است.")
    if kind == "donation":
        amount = data.get("amount")
        if type(amount) is not int or amount <= 0 or amount > 10**13:
            raise ValueError("مبلغ باید عدد صحیح مثبت باشد.")
        if data.get("currency") not in CURRENCIES:
            raise ValueError("واحد مبلغ باید SEK، IRT (تومان)، IRR (ریال) یا EUR باشد.")
        dt = data.get("date")
        if not isinstance(dt, str):
            raise ValueError("تاریخ پرداخت به شکل YYYY-MM-DD ضروری است.")
        date.fromisoformat(dt)
        ref = data.get("payment_reference")
        if ref is not None and (not isinstance(ref, str) or len(ref) > 100):
            raise ValueError("شمارهٔ پیگیری معتبر نیست.")
        code = data.get("campaign_code")
        if code is not None and (not isinstance(code, str) or len(code) > 20):
            raise ValueError("کد طرح معتبر نیست.")
        note = data.get("note")
        if note is not None and (not isinstance(note, str) or len(note) > 4000):
            raise ValueError("توضیح کمک باید متن کوتاه باشد.")
        if ref is not None:
            ref = ref.strip().upper()
        data = {"amount": amount, "currency": data["currency"], "date": dt,
                "payment_reference": ref or None, "campaign_code": code, "note": note}
    return {"kind": kind, "title": title.strip(), "body": body.strip(), "data": data}


class Store:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "hamdel.sqlite3"
        with self.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS updates(id INTEGER PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS sessions(chat_id TEXT PRIMARY KEY, thread TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS usage_events (
              event TEXT PRIMARY KEY, chat_id TEXT NOT NULL, model TEXT NOT NULL,
              usage_json TEXT NOT NULL, created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS drafts (
              id TEXT PRIMARY KEY, source TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
              title TEXT NOT NULL, body TEXT NOT NULL, payload TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending', created_at INTEGER NOT NULL,
              approved_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS voice_confirmations (
              id TEXT PRIMARY KEY, source TEXT NOT NULL UNIQUE, chat_id TEXT NOT NULL,
              transcript TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
              created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS donations (
              id TEXT PRIMARY KEY, draft_id TEXT NOT NULL UNIQUE REFERENCES drafts(id),
              date TEXT NOT NULL, amount INTEGER NOT NULL, currency TEXT NOT NULL,
              campaign_code TEXT, payment_reference TEXT, note TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS donations_ref_unique ON donations(currency, payment_reference)
              WHERE payment_reference IS NOT NULL AND payment_reference <> '';
            """)

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, timeout=20, isolation_level=None)
        con.execute("PRAGMA busy_timeout=20000")
        con.execute("PRAGMA journal_mode=WAL")
        try:
            yield con
        finally:
            con.close()

    def seen_update(self, update_id: int) -> bool:
        with self.connect() as db:
            return db.execute("SELECT 1 FROM updates WHERE id=?", (update_id,)).fetchone() is not None

    def mark_update(self, update_id: int):
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO updates(id) VALUES(?)", (update_id,))

    def get_thread(self, chat: int | str) -> str | None:
        with self.connect() as db:
            row = db.execute("SELECT thread FROM sessions WHERE chat_id=?", (str(chat),)).fetchone()
            return row[0] if row else None

    def record_usage(self, chat, event, model, usage):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO usage_events VALUES(?,?,?,?,?)",
                       (str(event), str(chat), model, json.dumps(usage), int(time.time())))

    def set_thread(self, chat: int | str, thread: str):
        with self.connect() as db:
            db.execute("INSERT INTO sessions(chat_id,thread) VALUES(?,?) ON CONFLICT(chat_id) DO UPDATE SET thread=excluded.thread", (str(chat), thread))

    def create_draft(self, source: str, raw: dict) -> tuple[str, bool]:
        obj = validate(raw)
        identifier = str(uuid.uuid5(uuid.NAMESPACE_URL, "hamdel:" + source))
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO drafts(id,source,kind,title,body,payload,created_at) VALUES(?,?,?,?,?,?,?)",
                       (identifier, source, obj["kind"], obj["title"], obj["body"], json.dumps(obj, ensure_ascii=False), int(time.time())))
            row = db.execute("SELECT id,source FROM drafts WHERE source=?", (source,)).fetchone()
        return row[0], row[0] == identifier

    def get_draft(self, draft_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT id,source,kind,title,body,payload,status FROM drafts WHERE id=?", (draft_id,)).fetchone()
        return dict(zip(("id", "source", "kind", "title", "body", "payload", "status"), row)) if row else None

    def list_drafts(self, status: str = "pending", limit: int = 30) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT id,kind,title,status FROM drafts WHERE status=? ORDER BY created_at DESC LIMIT ?", (status, limit)).fetchall()
        return [dict(zip(("id", "kind", "title", "status"), r)) for r in rows]

    def decide(self, draft_id: str, approve: bool) -> str:
        """Atomic, idempotent approval. Reject duplicate payment references."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT kind,payload,status FROM drafts WHERE id=?", (draft_id,)).fetchone()
            if row is None:
                db.execute("ROLLBACK")
                return "not_found"
            kind, encoded, status = row
            if status != "pending":
                db.execute("COMMIT")
                return status
            if approve and kind == "donation":
                data = json.loads(encoded)["data"]
                try:
                    db.execute("INSERT INTO donations(id,draft_id,date,amount,currency,campaign_code,payment_reference,note) VALUES(?,?,?,?,?,?,?,?)",
                       (str(uuid.uuid4()), draft_id, data["date"], data["amount"], data["currency"],
                        data.get("campaign_code"), data.get("payment_reference"), data.get("note")))
                except sqlite3.IntegrityError:
                    db.execute("ROLLBACK")
                    return "duplicate_reference"
            new_status = "approved" if approve else "rejected"
            db.execute("UPDATE drafts SET status=?,approved_at=? WHERE id=? AND status='pending'",
                       (new_status, int(time.time()) if approve else None, draft_id))
            db.execute("COMMIT")
            return new_status

    def summary(self) -> dict:
        with self.connect() as db:
            rows = db.execute("SELECT date,amount,currency,COALESCE(campaign_code,''),COALESCE(payment_reference,''),COALESCE(note,'') FROM donations ORDER BY date ASC").fetchall()
        totals = defaultdict(int)
        for _, amount, currency, code, _, _ in rows:
            totals[(currency, code or "بدون عنوان")] += amount
        return {
            "total_approved_transactions": len(rows),
            "by_currency_and_campaign": [
                {"currency": k[0], "campaign_code": k[1], "amount": v}
                for k, v in sorted(totals.items())
            ],
            "currency_rule": "IRR=ریال، IRT=تومان، SEK=کرون، EUR=یورو؛ بدون نرخ اعلامی کاربر تبدیل نکن.",
        }

    def export_approved_csv(self, destination: Path):
        with self.connect() as db:
            rows = db.execute("SELECT date,amount,currency,COALESCE(campaign_code,''),COALESCE(payment_reference,''),COALESCE(note,'') FROM donations ORDER BY date ASC").fetchall()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp = destination.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["date", "amount", "currency", "campaign_code", "payment_reference", "note"])
            writer.writerows(rows)
        temp.replace(destination)

    def get_offset(self) -> int:
        with self.connect() as db:
            row = db.execute("SELECT value FROM meta WHERE key='bale_offset'").fetchone()
            return int(row[0]) if row else 0

    def set_offset(self, offset: int):
        with self.connect() as db:
            db.execute("INSERT INTO meta(key,value) VALUES('bale_offset',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(offset),))

    def add_voice(self, source: str, chat_id: int | str, transcript: str) -> str:
        if not transcript.strip() or len(transcript) > 30000:
            raise ValueError("متن صوت برای بررسی مناسب نیست.")
        voice_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "hamdel-voice:" + source))
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO voice_confirmations(id,source,chat_id,transcript,created_at) VALUES(?,?,?,?,?)",
                       (voice_id, source, str(chat_id), transcript, int(time.time())))
        return voice_id

    def get_voice(self, voice_id: str, chat_id: int | str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT source,transcript,status FROM voice_confirmations WHERE id=? AND chat_id=?",
                             (voice_id, str(chat_id))).fetchone()
        return dict(zip(("source", "transcript", "status"), row)) if row else None

    def decide_voice(self, voice_id: str, chat_id: int | str, *, status: str):
        if status not in {"processed", "rejected"}:
            raise ValueError("Invalid voice status")
        with self.connect() as db:
            db.execute("UPDATE voice_confirmations SET status=? WHERE id=? AND chat_id=? AND status='pending'",
                       (status, voice_id, str(chat_id)))

    def approved_content(self, limit: int = 100) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT id,kind,title,body,payload,approved_at FROM drafts "
                              "WHERE status='approved' AND kind != 'donation' ORDER BY approved_at DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": a, "kind": b, "title": c, "body": d,
                 "data": json.loads(e).get("data", {}), "approved_at": f}
                for a, b, c, d, e, f in rows]
