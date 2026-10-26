"""Versioned runtime settings: change these in GitHub, not the hosting panel."""
import json
from pathlib import Path

DEFAULTS = {
    "agent_model": "gpt-6-luna", "agent_effort": "low", "agent_timeout": 240,
    "proofread_model": "gpt-6-luna", "proofread_effort": "low",
    "archive_result_limit": 6, "artifact_max_bytes": 18 * 1024 * 1024,
    "speech_provider": "disabled", "speech_model_file": "ggml-small-q5_1.bin",
    "speech_max_seconds": 120, "speech_chunk_seconds": 15,
    "speech_timeout": 600, "speech_threads": 1,
}

def settings():
    path = Path(__file__).resolve().parents[1] / "config.json"
    return DEFAULTS | (json.loads(path.read_text()) if path.exists() else {})
