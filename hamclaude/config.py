"""Settings come from environment variables; secrets are never stored in the repo."""
import os
from dataclasses import dataclass, field

DEFAULT_CATEGORIES = (
    "کمک مالی و واریز", "توزیع کمک غیرنقدی", "نیازمند و درخواست کمک",
    "مراسم و برنامه", "پیگیری و هماهنگی", "گزارش فعالیت میدانی", "سایر",
)

@dataclass
class Config:
    bot_token: str = ""
    anthropic_key: str = ""
    provider: str = "claudecode"   # "claudecode" (Claude subscription via Claude Code CLI), "cloudflare" (free tier) or "claude" (paid API key)
    claude_bin: str = "claude"
    claude_code_model: str = "sonnet"
    model: str = "claude-sonnet-5-5"
    llm_model: str = "@cf/qwen/qwen3-30b-a3b-fp8"
    vision_model: str = "@cf/meta/llama-3.2-11b-vision-instruct"
    cf_account: str = ""
    cf_token: str = ""
    asr_provider: str = "auto"      # auto = gemini if GEMINI_API_KEY is set, else cloudflare
    gemini_key: str = ""
    elevenlabs_key: str = ""
    keys_dir: str = ""           # where keys handed over from Telegram are stored (set by load())
    gemini_model: str = "gemini-3.8-flash"
    asr_model: str = "@cf/openai/whisper-large-v3-turbo"
    data_dir: str = "data"
    owner_id: str = ""          # Telegram user id of the owner (reports are delivered here, in private)
    group_chat_id: str = ""     # Telegram group that holds the reports
    report_sender: str = ""   # optional: only accept this Telegram @username in the group (empty = any member)
    quiet_seconds: int = 1800   # a report closes after this much silence (or at once on /report)
    max_images: int = 10
    max_file_bytes: int = 20 * 1024 * 1024
    port: int = 8080
    categories: tuple = field(default_factory=lambda: DEFAULT_CATEGORIES)

def load(env=None):
    e = os.environ if env is None else env
    g = lambda k, d="": (e.get(k) or d).strip()
    cats = tuple(c.strip() for c in g("HAMDEL_CATEGORIES").split("|") if c.strip()) or DEFAULT_CATEGORIES
    return Config(
        bot_token=g("TELEGRAM_BOT_TOKEN"), anthropic_key=g("ANTHROPIC_API_KEY"),
        provider=g("LLM_PROVIDER", "claudecode").casefold(), claude_bin=g("CLAUDE_BIN", "claude"), claude_code_model=g("CLAUDE_CODE_MODEL", "sonnet"), model=g("CLAUDE_MODEL", "claude-sonnet-5-5"),
        llm_model=g("CF_LLM_MODEL", "@cf/qwen/qwen3-30b-a3b-fp8"), vision_model=g("CF_VISION_MODEL", "@cf/meta/llama-3.2-11b-vision-instruct"),
        cf_account=g("CLOUDFLARE_ACCOUNT_ID"), cf_token=g("CLOUDFLARE_API_TOKEN"),
        asr_provider=g("ASR_PROVIDER", "auto").casefold(), gemini_key=g("GEMINI_API_KEY"), elevenlabs_key=g("ELEVENLABS_API_KEY"), keys_dir=g("KEYS_DIR", "/opt/hamdel/keys"), gemini_model=g("GEMINI_MODEL", "gemini-3.8-flash"),
        asr_model=g("ASR_MODEL", "@cf/openai/whisper-large-v3-turbo"),
        data_dir=g("DATA_DIR", "data"), owner_id=g("OWNER_ID"), group_chat_id=g("GROUP_CHAT_ID"),
        report_sender=g("REPORT_SENDER", "").lstrip("@").casefold(),
        quiet_seconds=int(g("QUIET_SECONDS", "1800")), max_images=int(g("MAX_IMAGES", "10")),
        port=int(g("PORT", "8080")), categories=cats)
