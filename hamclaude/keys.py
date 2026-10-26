"""API keys the owner hands over from Telegram (`/setkey elevenlabs <key>`): stored outside the data directory the agent can read."""
import os
from pathlib import Path

NAMES = ("elevenlabs", "gemini")

def get(cfg, name):
    env = {"elevenlabs": cfg.elevenlabs_key, "gemini": cfg.gemini_key}.get(name, "")
    if env: return env
    if not cfg.keys_dir: return ""
    try: return (Path(cfg.keys_dir) / name).read_text().strip()
    except OSError: return ""

def put(cfg, name, value):
    if not cfg.keys_dir: raise RuntimeError("keys_dir_not_configured")
    d = Path(cfg.keys_dir); d.mkdir(parents=True, exist_ok=True)
    p = d / name; p.write_text(value.strip()); os.chmod(p, 0o600)
