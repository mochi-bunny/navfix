"""All settings come from .env so nothing secret lives in code."""
import os

from dotenv import load_dotenv

load_dotenv()


def _int(name: str) -> int:
    return int(os.environ[name])


def _opt_int(name: str):
    value = os.getenv(name, "").strip()
    return int(value) if value else None


DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]

CHANNELS = {
    "red-room": _int("CH_RED_ROOM"),
    "dispatch-recall": _int("CH_DISPATCH_RECALL"),
    "daily-checkin": _int("CH_DAILY_CHECKIN"),
    "approvals": _int("CH_APPROVALS"),
    "cell-globex": _int("CH_CELL_GLOBEX"),
    "ops-log": _int("CH_OPS_LOG"),
}

# Hourly telemetry health check (POST /telemetry). Optional until the channel exists.
CH_MONITOR_BOT = _opt_int("CH_MONITOR_BOT")
TELEMETRY_STALE_MIN = float(os.getenv("TELEMETRY_STALE_MIN", "75"))

ROLE_APPROVER = _int("ROLE_APPROVER")
ROLE_FIELD_ENGINEER = _opt_int("ROLE_FIELD_ENGINEER")
ROLE_CUSTOMER = _opt_int("ROLE_CUSTOMER")

LISTEN_HOST = os.getenv("LISTEN_HOST", "127.0.0.1")
LISTEN_PORT = int(os.getenv("LISTEN_PORT", "8787"))
SUPERVISOR_DECISION_URL = os.environ["SUPERVISOR_DECISION_URL"]
SHARED_KEY = os.getenv("BOT_SHARED_KEY", "")

LLM_ENABLED = os.getenv("LLM_ENABLED", "1") == "1"
LLM_URL = os.getenv("LLM_URL", "http://127.0.0.1:8000/v1").rstrip("/")
LLM_MODEL = os.getenv("LLM_MODEL", "")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "8"))

DB_PATH = os.getenv("DB_PATH", "data/bot.db")
MAX_UPLOAD_MB = float(os.getenv("MAX_UPLOAD_MB", "10"))
MAX_CLIPS = int(os.getenv("MAX_CLIPS", "2"))
