# ############################################################################################
import logging
import time
import threading
try:
    from sdnotify import SystemdNotifier
    SDNOTIFY_AVAILABLE = True
except ImportError:
    SDNOTIFY_AVAILABLE = False
    SystemdNotifier = None
from pyrogram import enums
import re
from CONFIG.config import Config
from HELPERS.safe_messeger import safe_send_message
from services.stats_events import capture_message_context

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log', mode='a', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

def close_logger():
    """Close all logging handlers to prevent file descriptor leaks"""
    try:
        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)
        logger.info("Logger handlers closed successfully")
    except Exception as e:
        logger.error(f"Error closing logger handlers: {e}")

# WatchDog
if SDNOTIFY_AVAILABLE:
    notifier = SystemdNotifier()
    
    def watchdog_loop():
        while True:
            notifier.notify("WATCHDOG=1")
            logger.info("[Watchdog] Sent WATCHDOG=1")
            time.sleep(30)  # Frequency is less than WatchdogSec

    # Start watchdog thread
    threading.Thread(target=watchdog_loop, daemon=True).start()

    # At the beginning of initialization
    notifier.notify("READY=1")
    logger.info("[Watchdog] Sent READY=1")
else:
    logger.info("[Watchdog] SystemdNotifier not available - watchdog disabled")
# Utility: pick proper log channel per kind

def get_log_channel(kind: str = "general", nsfw: bool = False, paid: bool = False) -> int:
    """Returns the appropriate Telegram chat ID for logs.

    kind: "general" | "video" | "image"
    nsfw: if True and kind is media, route to NSFW channel
    paid: if True, route to paid media channel
    """
    try:
        kind_normalized = (kind or "general").strip().lower()
        if paid:
            # For paid media never fallback to general LOGS_ID
            return getattr(Config, "LOGS_PAID_ID", 0) or getattr(Config, "LOG_EXCEPTION", 0)
        if nsfw and kind_normalized in ("video", "image"):
            # For NSFW media never fallback to general LOGS_ID
            return getattr(Config, "LOGS_NSFW_ID", 0) or getattr(Config, "LOG_EXCEPTION", 0)
        if kind_normalized == "video":
            # For video never fallback to general LOGS_ID
            return getattr(Config, "LOGS_VIDEO_ID", 0) or getattr(Config, "LOG_EXCEPTION", 0)
        if kind_normalized == "image":
            # For image never fallback to general LOGS_ID
            return getattr(Config, "LOGS_IMG_ID", 0) or getattr(Config, "LOG_EXCEPTION", 0)
        # General messages and other kinds use LOGS_ID
        return getattr(Config, "LOGS_ID", 0)
    except Exception:
        # In case of unexpected errors avoid misrouting media to LOGS_ID
        if kind in ("video", "image") or nsfw or paid:
            return getattr(Config, "LOG_EXCEPTION", 0)
        return getattr(Config, "LOG_EXCEPTION", getattr(Config, "LOGS_ID", 0))

# Send Message to Logger


# --- Log channel cards ------------------------------------------------------------
def _plain_log_text(msg) -> str:
    """Old log texts carry their own HTML; keep it short and safe for one card."""
    text = re.sub(r"</?(?:blockquote|pre|code|tg-emoji|emoji)[^>]*>", "", str(msg or "")).strip()
    return text[:700] + ("…" if len(text) > 700 else "")


_NOISE = ("cookie", "menu opened", "proxy", "nsfw", "cache", "script", "browser", "help shown", "status")
_SENT = ("sent", "upload complete", "upload completed", "reposted")


def _log_kind(msg):
    """Old log texts -> (icon, title, short body) for the channel, or None for internal noise."""
    plain = re.sub(r"<[^>]+>", "", str(msg or "")).strip()
    low = plain.lower()
    if "entered a url" in low or "entered a link" in low:
        return None   # log_download() writes the card, with the video title
    if any(k in low for k in _SENT):
        short = re.sub(r"\s*to user\s*\d+", "", plain.split("\n")[0])
        short = re.sub(r"https?://\S+", "", short).strip(" :·-")
        short = {"Video upload completed with file splitting.": "Video sent in parts"}.get(short, short)
        return "✅", "Sent", short[:120]
    if any(k in low for k in _NOISE):
        return None
    return "📝", "Log", plain[:300]


def _short_error(msg) -> str:
    """The old error template (cookie advice, /clean, guide links, code, description, full error)
    -> the friendly reason + one line of the real error."""
    plain = re.sub(r"<[^>]+>", "", str(msg or ""))
    raw = ""
    m = re.search(r"Description:\s*(.+)", plain) or re.search(r"(ERROR:.+)", plain)
    if m:
        raw = m.group(1).strip()
    elif plain.strip():
        raw = plain.strip().split("\n")[-1]
    reason = re.sub(r"<[^>]+>", "", friendly_error(raw or plain)).strip().split("\n")[0] if raw else ""
    from html import escape
    raw = escape(raw[:300])
    return (f"<b>{escape(reason)}</b>\n" if reason and reason != raw else "") + (f"<code>{raw}</code>" if raw else "")


def log_card(message, icon: str, title: str, msg, url: str = None) -> str:
    """Card for the log channels: user (clickable name, @username, ID), platform + link, event, time."""
    from datetime import datetime, timedelta, timezone
    from html import escape
    chat = getattr(message, "chat", None)
    user = getattr(message, "from_user", None) or chat
    uid = getattr(user, "id", None) or getattr(chat, "id", 0)
    name = " ".join(x for x in (getattr(user, "first_name", None), getattr(user, "last_name", None)) if x) or "User"
    uname = getattr(user, "username", None)
    who = (f'<a href="tg://user?id={uid}">{escape(name[:60])}</a>'
           + (f" · @{escape(uname)}" if uname else "") + f" · <code>{uid}</code>")
    link = (url or _extract_url_from_message(message) or "").strip()
    link = re.sub(r"[?&]si=[\w-]+$", "", link)  # YouTube share tracking
    site_line = ""
    if link:
        try:
            from COMMANDS.simple_ui import detect_platform, PLATFORMS
            found = detect_platform(link)
            site = f"{PLATFORMS[found[0]][1]} {PLATFORMS[found[0]][0]}" if found else "🌐 Link"
        except Exception:
            site = "🌐 Link"
        short = escape(link if len(link) <= 60 else link[:57] + "…")
        site_line = f'\n{site} · <a href="{escape(link, quote=True)}">{short}</a>'
    when = datetime.now(timezone(timedelta(hours=5, minutes=30))).strftime("%d %b %Y · %I:%M %p")
    body = "\n".join(l for l in _plain_log_text(msg).split("\n")
                     if l.strip() and not re.match(r"\s*(user'?s name|url|user id|name)\s*:", l, re.I))
    return (f"{icon} <b>{title}</b>\n<blockquote>👤 {who}{site_line}</blockquote>"
            + (f"\n{body}" if body else "") + f"\n🕐 <i>{when} IST</i>")


def log_download(message, title=None, detail="", url=None):
    """'Downloading' card in the download-log channel: who, which video (title), quality, link."""
    from html import escape
    if not get_log_channel("general"):
        return
    body = (f"🎬 <b>{escape(str(title)[:200])}</b>" if title else "") + (f"\n{escape(detail)}" if detail else "")
    try:
        safe_send_message(get_log_channel("general"), log_card(message, "📥", "Downloading", body, url),
                          parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
    except Exception as exc:
        logger.info(f"[log] download card failed: {exc}")


def send_to_logger(message, msg):
    capture_message_context(message)
    kind = _log_kind(msg)
    if kind is None or not get_log_channel("general"):  # internal noise / no log channel set
        logger.info(f"[log] {str(msg)[:300]}")
        return
    icon, title, body = kind
    safe_send_message(get_log_channel("general"), log_card(message, icon, title, body),
                      parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)

# Send Message to User Only

# --- Short user-facing errors ---------------------------------------------------
# Only raw technical dumps are rewritten; normal bot texts (cookie help etc.) pass through untouched.
_RAW_ERROR_HINTS = ("ERROR:", "Traceback", "Error Code", "Full Error", "HTTP Error", "Unknown error",
                    "DownloadError", "ExtractorError", "Unsupported URL", "Errno")
_FRIENDLY = (
    (("sign in to confirm", "not a bot"), "ban", "YouTube asked for a login check.",
     "Please try again in a few minutes."),
    (("private", "login", "log in", "sign in", "members-only", "age", "cookies", "authentication", "401", "403",
      "empty media response"), "lips", "This link is private or needs login.",
     "Only public posts can be downloaded."),
    (("looks truncated", "incomplete youtube id", "invalid url", "is not a valid url"), "warn",
     "This link is incomplete or broken.", "Copy the full link and send it again."),
    (("available in your country", "not available in your country", "geo restriction", "blocked in your country"),
     "globe", "This video is blocked in the bot's region.",
     "The uploader allows it only in some countries, so it can't be downloaded here."),
    (("not available", "unavailable", "removed", "deleted", "404", "does not exist", "no video"), "ban",
     "This post is unavailable.", "It may be deleted, region-locked or the link is wrong."),
    (("too large", "file size", "exceeds", "limit"), None, None, None),  # keep the original size-limit text
    (("timed out", "timeout", "connection", "network", "temporarily"), "globe", "Network problem.",
     "Please try again in a minute."),
)


def _friendly_text(icon, title, body) -> str:
    try:
        from COMMANDS.ui_kit import pem
        icon = pem(icon)
    except Exception:
        icon = "⚠️"
    return f"{icon} <b>{title}</b>\n<blockquote><b>{body}</b></blockquote>"


def friendly_error(msg) -> str:
    """Turn raw yt-dlp/gallery-dl dumps into one short message. Normal texts pass through."""
    text = str(msg or "")
    if not any(h.lower() in text.lower() for h in _RAW_ERROR_HINTS):
        return text
    low = text.lower()
    for keys, icon, title, body in _FRIENDLY:
        if any(k in low for k in keys):
            return _friendly_text(icon, title, body) if title else text
    return _friendly_text("warn", "Download failed.",
                          "Please try again. If it keeps failing, the post may be private or removed.")


def send_to_user(message, msg):
    capture_message_context(message)
    user_id = message.chat.id
    friendly = friendly_error(msg)
    if friendly != msg:
        logger.info(f"[user-error] {user_id}: {str(msg)[:500]}")
        try:  # a raw download error reached the user: tell the owners (grouped)
            from HELPERS.alerts import download_failed
            download_failed(user_id, _extract_url_from_message(message) or "", str(msg))
        except Exception:
            pass
    safe_send_message(user_id, friendly, parse_mode=enums.ParseMode.HTML, message=message)

# Send Message to All ...

def send_to_all(message, msg, parse_mode=None):
    capture_message_context(message)
    user_id = message.chat.id
    if get_log_channel("general"):  # 0 = no log channel set
        safe_send_message(get_log_channel("general"), log_card(message, "📣", "Bot log", msg),
                          parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
    safe_send_message(user_id, msg, parse_mode=parse_mode or enums.ParseMode.HTML, message=message)

# --- Helpers for error logging -------------------------------------------------

def _extract_url_from_message(message) -> str:
    """Best-effort extraction of the first URL from user message text/caption."""
    try:
        text = getattr(message, "text", None) or getattr(message, "caption", None) or ""
        if not text:
            return ""
        match = re.search(r"https?://\S+", text)
        return match.group(0) if match else ""
    except Exception:
        return ""

# Send Error Message to User and LOG_EXCEPTION channel
def send_error_to_user(message, msg, url: str = None):
    capture_message_context(message)
    """Send error message to user and log it to LOG_EXCEPTION channel.

    url: optional explicit URL that caused the error; if not provided, will be
         extracted from the user's message text/caption when possible.
    """
    user_id = message.chat.id
    # Send to LOG_EXCEPTION channel for error tracking
    if getattr(Config, "LOG_EXCEPTION", 0):  # 0 = no log channel set
        safe_send_message(Config.LOG_EXCEPTION, log_card(message, "⚠️", "Error", _short_error(msg), url),
                          parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
    # Send to user: short message; full details stay in LOG_EXCEPTION / logs
    friendly = friendly_error(msg)
    if friendly != msg:
        logger.info(f"[user-error] {user_id}: {str(msg)[:500]}")
        try:  # a raw download error reached the user: tell the owners (grouped)
            from HELPERS.alerts import download_failed
            download_failed(user_id, _extract_url_from_message(message) or "", str(msg))
        except Exception:
            pass
    safe_send_message(user_id, friendly, parse_mode=enums.ParseMode.HTML, message=message)

# Log error message to LOG_EXCEPTION channel (without sending to user)
def log_error_to_channel(message, msg, url: str = None):
    capture_message_context(message)
    """Log error message to LOG_EXCEPTION channel only.

    url: optional explicit URL that caused the error; if not provided, will be
         extracted from the user's message text/caption when possible.
    """
    # Send to LOG_EXCEPTION channel for error tracking
    if getattr(Config, "LOG_EXCEPTION", 0):  # 0 = no log channel set
        safe_send_message(Config.LOG_EXCEPTION, log_card(message, "⚠️", "Error", _short_error(msg), url),
                          parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
