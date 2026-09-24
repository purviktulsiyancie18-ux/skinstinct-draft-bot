"""Settings, loaded from .env. Nothing secret lives in code."""
import os
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", message=".*OpenSSL.*")

import logging  # noqa: E402

from dotenv import load_dotenv  # noqa: E402

logging.getLogger("google_genai").setLevel(logging.ERROR)  # hides "non-text parts" noise

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


def _int_or_none(value):
    value = (value or "").strip()
    return int(value) if value else None


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

# Meera's private chat with the bot. Drafts go here and only here.
OWNER_CHAT_ID = _int_or_none(os.getenv("OWNER_CHAT_ID"))
# Optional: the channel she drops notes into (bot must be an admin there).
SOURCE_CHANNEL_ID = _int_or_none(os.getenv("SOURCE_CHANNEL_ID"))

TRIAGE_MODEL = os.getenv("GEMINI_TRIAGE_MODEL", "gemini-3.6-flash")
RESEARCH_MODEL = os.getenv("GEMINI_RESEARCH_MODEL", "gemini-3.6-flash")
DRAFT_MODEL = os.getenv("GEMINI_DRAFT_MODEL", "gemini-3.1-pro-preview")

# Pacing: roughly three drafts a week, delivered on these days at this time.
DELIVERY_DAYS = [d.strip().lower()[:3] for d in os.getenv("DELIVERY_DAYS", "mon,wed,fri").split(",") if d.strip()]
DELIVERY_TIME = os.getenv("DELIVERY_TIME", "08:30")
TIMEZONE = os.getenv("TIMEZONE", "Asia/Kolkata")

# Notes scoring below this are discarded even if the model says "develop".
MIN_SCORE = int(os.getenv("MIN_SCORE", "6"))

DB_PATH = ROOT / os.getenv("DB_PATH", "skinstinct_drafts.db")
VOICE_GUIDE_PATH = ROOT / "Meera_Pillai_Voice_Guide.txt"
PUBLISHED_DIR = ROOT / "voice" / "published"


def require(*names):
    missing = [n for n in names if not globals().get(n)]
    if missing:
        raise SystemExit(f"Missing in .env: {', '.join(missing)} (see .env.example)")
