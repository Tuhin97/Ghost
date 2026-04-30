"""
config.py — Central configuration loaded from .env
Every other module imports from here. Never read os.environ directly elsewhere.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).parent / ".env")


def _require(key: str) -> str:
    """Raise clearly if a required env var is missing."""
    val = os.getenv(key)
    if not val:
        raise EnvironmentError(
            f"Missing required environment variable: {key}\n"
            f"Copy .env.example to .env and fill in your values."
        )
    return val


def _optional(key: str, default: str = "") -> str:
    return os.getenv(key, default)


# ── Claude API ────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY: str = _require("ANTHROPIC_API_KEY")

# Model selection — swap models in one place
MODEL_BEST  = "claude-opus-4-5"     # complex reasoning, architecture decisions
MODEL_SMART = "claude-sonnet-4-5"   # commands, code, CV tailoring
MODEL_FAST  = "claude-haiku-4-5"    # monitoring, ranking, patterns

# ── JWT Auth ──────────────────────────────────────────────────────────────────
JWT_SECRET: str = _require("JWT_SECRET")
JWT_ALGORITHM   = "HS256"
JWT_EXPIRE_DAYS = 30

DEVICE_TOKENS: dict = {
    "samsung":  _optional("DEVICE_TOKEN_SAMSUNG"),
    "macbook":  _optional("DEVICE_TOKEN_MACBOOK"),
    "thinkpad": _optional("DEVICE_TOKEN_THINKPAD"),
    "iphone":   _optional("DEVICE_TOKEN_IPHONE"),
}

# ── Network ───────────────────────────────────────────────────────────────────
THINKPAD_TAILSCALE_IP: str = _optional("THINKPAD_TAILSCALE_IP", "127.0.0.1")
SERVER_HOST: str = THINKPAD_TAILSCALE_IP
SERVER_PORT: int = 8000

# ── Device SSH ────────────────────────────────────────────────────────────────
MAC_HOST: str     = _optional("MAC_HOST")
MAC_USER: str     = _optional("MAC_USER")
LINUX_USER: str   = _optional("LINUX_USER", "tuhin")
SSH_KEY_PATH: str = _optional("SSH_KEY_PATH", "~/.ssh/ghost_key")

# Safe search paths — file agent refuses to search outside these
SAFE_SEARCH_PATHS = [
    "~/Documents",
    "~/Downloads",
    "~/Desktop",
    "~/Projects",
    "~/Code",
]

# ── Google APIs ───────────────────────────────────────────────────────────────
GMAIL_CREDENTIALS_PATH: str = _optional("GMAIL_CREDENTIALS_PATH", "./credentials.json")

# ── Voice ─────────────────────────────────────────────────────────────────────
PIPER_MODEL_PATH: str = _optional("PIPER_MODEL_PATH", "./voice/models/en_US-lessac-medium.onnx")
PIPER_SAMPLE_RATE: int = 22050  # sample rate for lessac-medium model
ELEVEN_API_KEY: str  = _optional("ELEVEN_API_KEY")
ELEVEN_VOICE_ID: str = _optional("ELEVEN_VOICE_ID")

# ── MQTT ──────────────────────────────────────────────────────────────────────
MQTT_HOST: str     = _optional("MQTT_HOST", "localhost")
MQTT_PORT: int     = int(_optional("MQTT_PORT", "1883"))
MQTT_USER: str     = _optional("MQTT_USER", "ghost")
MQTT_PASSWORD: str = _optional("MQTT_PASSWORD")

# ── GitHub ────────────────────────────────────────────────────────────────────
GITHUB_TOKEN: str   = _optional("GITHUB_TOKEN")
GITHUB_REPOS: list  = [
    r.strip()
    for r in _optional("GITHUB_REPOS", "").split(",")
    if r.strip()
]

# ── Home Assistant ────────────────────────────────────────────────────────────
HOME_ASSISTANT_URL: str   = _optional("HOME_ASSISTANT_URL")
HOME_ASSISTANT_TOKEN: str = _optional("HOME_ASSISTANT_TOKEN")

# ── Web Search ────────────────────────────────────────────────────────────────
TAVILY_API_KEY: str = _optional("TAVILY_API_KEY")

# ── Memory ────────────────────────────────────────────────────────────────────
MEMORY_DIR = Path(__file__).parent / "data" / "ghost_memory"
MEMORY_DIR.mkdir(parents=True, exist_ok=True)

_raw_key = _optional("MEMORY_ENCRYPTION_KEY")
if not _raw_key:
    from cryptography.fernet import Fernet
    _raw_key = Fernet.generate_key().decode()
    print(
        f"\n[Ghost] No MEMORY_ENCRYPTION_KEY found.\n"
        f"Add this to your .env:\n\n"
        f"  MEMORY_ENCRYPTION_KEY={_raw_key}\n"
    )
MEMORY_ENCRYPTION_KEY: str = _raw_key
MEMORY_RECALL_COUNT = 5

# ── Neo4j (Weekend 13+) ───────────────────────────────────────────────────────
NEO4J_URI: str      = _optional("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER: str     = _optional("NEO4J_USER", "neo4j")
NEO4J_PASSWORD: str = _optional("NEO4J_PASSWORD")

# ── Monitoring thresholds ─────────────────────────────────────────────────────
COST_ALERT_MONTHLY_USD: float = float(_optional("COST_ALERT_MONTHLY_USD", "15"))
POLL_INTERVAL_SECONDS  = 30
DISK_WARN_PCT          = 85
DISK_CRIT_PCT          = 95
CPU_WARN_PCT           = 90
RAM_WARN_PCT           = 90
DEVICE_OFFLINE_SECONDS = 90
GITHUB_POLL_MINUTES    = 15
BRIEFING_HOUR          = 9      # hour to send morning briefing (UTC)

# ── Rate limiting ─────────────────────────────────────────────────────────────
RATE_LIMIT = "30/minute"

# ── Data paths ────────────────────────────────────────────────────────────────
DATA_DIR        = Path(__file__).parent / "data"
API_USAGE_DB    = DATA_DIR / "api_usage.db"
JOB_TRACKER_DB  = DATA_DIR / "job_tracker.db"
DATA_DIR.mkdir(parents=True, exist_ok=True)
