"""Local readiness checks. Never print credentials or user ids."""
import importlib.util
import os
from pathlib import Path
import shutil


def main():
    checks = {
        "BALE_BOT_TOKEN configured": bool(os.getenv("BALE_BOT_TOKEN")),
        "Authorized admins configured": bool(os.getenv("BALE_ALLOWED_USER_IDS", "").strip()),
        "Codex CLI installed": bool(shutil.which("codex")),
        "CODEX_HOME directory exists": Path(os.getenv("CODEX_HOME", str(Path.home() / ".codex"))).exists(),
        "Persian ASR package installed": importlib.util.find_spec("faster_whisper") is not None,
    }
    print("همدل — بررسی آمادگی (بدون نمایش اطلاعات محرمانه)")
    for name, ok in checks.items():
        print(("✓ " if ok else "✗ ") + name)
    print("نکته: این بررسی ورود معتبر Codex، اتصال بله یا کیفیت ویس را تست نمی‌کند.")
    return 0 if all(checks.values()) else 1

if __name__ == "__main__":
    raise SystemExit(main())
