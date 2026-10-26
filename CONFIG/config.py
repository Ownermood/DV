"""Environment-backed runtime configuration for All Downloader.

The upstream project's full Config class remains in CONFIG/_config.py.
Secrets and deployment-specific IDs are overridden by environment variables.
"""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
except ImportError:
    pass

from CONFIG._config import Config  # noqa: E402


def _int(name, default=0):
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value.strip())
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc


def _int_list(name, default):
    value = os.getenv(name)
    if not value:
        return default
    try:
        return [int(x.strip()) for x in value.split(",") if x.strip()]
    except ValueError as exc:
        raise RuntimeError(f"{name} must be comma-separated integer IDs") from exc


# Required Telegram credentials
Config.BOT_TOKEN = os.getenv("BOT_TOKEN", Config.BOT_TOKEN).strip()
Config.API_ID = _int("API_ID", Config.API_ID)
Config.API_HASH = os.getenv("API_HASH", Config.API_HASH).strip()
Config.ADMIN = _int_list("ADMIN_IDS", Config.ADMIN)
Config.ALLOWED_GROUP = _int_list("ALLOWED_GROUP_IDS", Config.ALLOWED_GROUP)

# Logging and force-join configuration
for _name in ("LOGS_ID", "LOGS_VIDEO_ID", "LOGS_NSFW_ID", "LOGS_IMG_ID", "LOGS_PAID_ID", "LOG_EXCEPTION", "SUBSCRIBE_CHANNEL"):
    setattr(Config, _name, _int(_name, getattr(Config, _name, 0)))
Config.SUBSCRIBE_CHANNEL_URL = os.getenv("SUBSCRIBE_CHANNEL_URL", Config.SUBSCRIBE_CHANNEL_URL).strip()
Config.BOT_NAME = os.getenv("BOT_NAME", Config.BOT_NAME).strip()
Config.BOT_NAME_FOR_USERS = os.getenv("BOT_NAME_FOR_USERS", Config.BOT_NAME_FOR_USERS).strip()
Config.DASHBOARD_USERNAME = os.getenv("DASHBOARD_USERNAME", Config.DASHBOARD_USERNAME).strip()
Config.DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", Config.DASHBOARD_PASSWORD)

# Safe defaults for optional services
Config.USE_FIREBASE = os.getenv("USE_FIREBASE", "false").lower() in {"1", "true", "yes", "on"}
Config.MAX_FILE_SIZE_GB = float(os.getenv("MAX_FILE_SIZE_GB", "4"))
# Optional welcome image for /start: https URL or Telegram file_id
Config.START_IMAGE = os.getenv("START_IMAGE", getattr(Config, "START_IMAGE", "")).strip()
Config.DOWNLOAD_TIMEOUT = int(os.getenv("DOWNLOAD_TIMEOUT", str(Config.DOWNLOAD_TIMEOUT)))

if os.getenv("REQUIRE_VALID_CONFIG", "true").lower() in {"1", "true", "yes", "on"}:
    missing = []
    if not Config.BOT_TOKEN or ":" not in Config.BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if Config.API_ID <= 0:
        missing.append("API_ID")
    if not Config.API_HASH:
        missing.append("API_HASH")
    if not Config.ADMIN or all(admin <= 0 for admin in Config.ADMIN):
        missing.append("ADMIN_IDS")
    if not Config.DASHBOARD_PASSWORD or len(Config.DASHBOARD_PASSWORD) < 16 or "replace-with-" in Config.DASHBOARD_PASSWORD:
        missing.append("DASHBOARD_PASSWORD (minimum 16 characters)")
    if Config.SUBSCRIBE_CHANNEL == 0 or not Config.SUBSCRIBE_CHANNEL_URL:
        missing.append("SUBSCRIBE_CHANNEL and SUBSCRIBE_CHANNEL_URL (force-join channel)")
    if missing:
        raise RuntimeError("Missing/invalid required configuration: " + ", ".join(missing) + ". Copy .env.example to .env and fill it in.")
