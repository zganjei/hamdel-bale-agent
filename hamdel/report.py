"""Deterministic local report summaries using only approved donations."""
from __future__ import annotations
import argparse
import csv
import json
from collections import defaultdict
from datetime import date
from pathlib import Path


def calculate(csv_path: Path, start: str, end: str, rate_toman_per_sek: int | None = None):
    date.fromisoformat(start)
    date.fromisoformat(end)
    if start > end:
        raise ValueError("تاریخ شروع باید قبل از پایان باشد.")
    if rate_toman_per_sek is not None and rate_toman_per_sek <= 0:
        raise ValueError("نرخ کرون باید مثبت باشد.")
    totals = defaultdict(int)
    detail = []
    with Path(csv_path).open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if start <= row["date"] <= end:
                amount = int(row["amount"])
                key = (row["currency"], row["campaign_code"] or "بدون عنوان")
                totals[key] += amount
                detail.append(row)
    summary = [{"currency": currency, "campaign_code": code, "amount": amount}
               for (currency, code), amount in sorted(totals.items())]
    output = {"from": start, "to": end, "count": len(detail), "summary": summary,
              "notes": "اعداد فقط از کمک‌های تأییدشده آمده‌اند. جمع بین واحدهای متفاوت انجام نشده است."}
    if rate_toman_per_sek is not None:
        sek = sum(v for (unit, _), v in totals.items() if unit == "SEK")
        output["sek_to_toman"] = {"rate_toman_per_sek": rate_toman_per_sek,
                                  "sek_total": sek, "toman_equivalent": sek * rate_toman_per_sek}
    return output


def main():
    p = argparse.ArgumentParser(description="Aggregate approved charity donations")
    p.add_argument("--csv", default="context/approved_donations.csv")
    p.add_argument("--from-date", required=True)
    p.add_argument("--to-date", required=True)
    p.add_argument("--rate-toman-per-sek", type=int)
    args = p.parse_args()
    print(json.dumps(calculate(Path(args.csv), args.from_date, args.to_date, args.rate_toman_per_sek), ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
