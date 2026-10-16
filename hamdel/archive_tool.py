"""Read a bounded local archive snapshot without filling the model context."""
import argparse
import json
import re
from pathlib import Path

def search(path, query="", start=None, end=None, limit=6, message_id=None):
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    terms = re.findall(r"\w+", query.casefold())
    result = []
    for row in rows:
        date = row.get("date_utc", "")[:10]
        if (start and date < start) or (end and date > end):
            continue
        if message_id is not None and row.get("id") != message_id:
            continue
        text = (row.get("text") or "") + " " + (row.get("media_summary") or "")
        score = sum(t in text.casefold() for t in terms)
        if terms and not score:
            continue
        result.append((score, row))
    result.sort(key=lambda x: (x[0], x[1].get("date_utc", ""), x[1]["id"]), reverse=True)
    return {"matched": len(result), "returned": min(len(result), limit),
            "sources": [{k: (v[:1800] if isinstance(v, str) else v) for k, v in row.items()}
                        for _, row in result[:max(1, min(limit, 20))]]}

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--query", default="")
    p.add_argument("--from-date")
    p.add_argument("--to-date")
    p.add_argument("--message-id", type=int)
    p.add_argument("--limit", type=int, default=6)
    args = p.parse_args()
    root = Path(__file__).resolve().parent.parent
    print(json.dumps(search(root / "context/archive_index.json", args.query,
          args.from_date, args.to_date, args.limit, args.message_id), ensure_ascii=False))
