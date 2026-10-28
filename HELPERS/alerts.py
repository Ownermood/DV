"""Owner alerts: failed downloads (grouped, at most one DM per kind every 15 min)
and a health watcher (PO token provider, MongoDB, free disk) every 5 min.
"""
from __future__ import annotations

import html
import os
import shutil
import threading
import time
from urllib.parse import urlparse

from HELPERS.logger import logger

FAIL_WINDOW = 15 * 60
HEALTH_EVERY = 5 * 60
MIN_FREE_GB = 2

_lock = threading.Lock()
_fails = {}          # (site, kind) -> {"last": ts, "count": n since last alert, "example": (user, url, error)}
_health = {}         # check name -> True (ok) / False (down)


def _owners_dm(text: str) -> None:
    try:
        from COMMANDS.cookie_store import alert_owners
        alert_owners(text)
    except Exception as exc:
        logger.warning(f"[alerts] DM failed: {exc}")


# The user's own problem (bad / private / deleted link, too big): nothing for the owners to fix
USER_SIDE = ("looks truncated", "incomplete youtube id", "invalid url", "unsupported url", "is not a valid url",
             "private video", "video unavailable", "this video is unavailable", "has been removed", "not available",
             "does not exist", "404", "too large", "file size", "exceeds", "members-only", "age-restricted",
             "no video formats", "no video in this post", "premieres in", "live event will begin")


def _real_error(error: str) -> str:
    """The one line that matters out of the old error templates (HTML, cookie advice, codes)."""
    import re
    plain = re.sub(r"<[^>]+>", "", str(error or ""))
    m = re.search(r"Description:\s*(.+)", plain) or re.search(r"(ERROR:.+)", plain)
    return (m.group(1) if m else plain.strip().split("\n")[-1] if plain.strip() else "").strip()


def _kind(error: str) -> str:
    low = (error or "").lower()
    for key, kind in (("not a bot", "YouTube bot check"), ("sign in", "login needed"), ("login", "login needed"),
                      ("cookie", "cookie problem"), ("403", "HTTP 403 (blocked)"), ("429", "rate limited"),
                      ("timed out", "timeout"), ("timeout", "timeout"), ("connection", "network")):
        if key in low:
            return kind
    return "other error"


def download_failed(user_id, url: str, error: str) -> None:
    """A user saw a download error. The first one of a kind DMs the owners; repeats are counted."""
    error = _real_error(error)
    if not error or any(k in error.lower() for k in USER_SIDE):
        return  # the user's link, not the bot: no DM
    site = (urlparse(url or "").hostname or "unknown").replace("www.", "")
    key = (site, _kind(error))
    now = time.time()
    with _lock:
        st = _fails.setdefault(key, {"last": 0.0, "count": 0})
        st["count"] += 1
        st["example"] = (user_id, url, error)
        if now - st["last"] < FAIL_WINDOW:
            return
        count, st["count"], st["last"] = st["count"], 0, now
    user, link, err = st["example"]
    _owners_dm(f"⚠️ <b>Download failed</b> · {html.escape(key[0])} · {html.escape(key[1])}\n"
               f"<blockquote><b>{count}× in the last {FAIL_WINDOW // 60} min</b>\n"
               f"User: <code>{user}</code>\nLink: {html.escape((link or '')[:200])}\n"
               f"Error: <code>{html.escape(str(err)[:300])}</code></blockquote>")


# ---------------- health ----------------
def _check_pot() -> bool:
    import requests
    from CONFIG.config import Config
    base = getattr(Config, "YOUTUBE_POT_BASE_URL", "")
    if not base or not getattr(Config, "YOUTUBE_POT_ENABLED", False):
        return True
    return requests.get(base.rstrip("/") + "/ping", timeout=10).ok


def _check_mongo() -> bool:
    from DATABASE import mongo_db
    if not mongo_db.enabled():
        return True
    mongo_db.database().client.admin.command("ping")
    return True


def _check_disk() -> bool:
    return shutil.disk_usage(os.getcwd()).free / 1024 ** 3 >= MIN_FREE_GB


CHECKS = {"YouTube token provider (bgutil)": _check_pot, "MongoDB": _check_mongo,
          f"Free disk (≥ {MIN_FREE_GB} GB)": _check_disk}


def health_snapshot() -> dict:
    """{check name: True/False} right now."""
    out = {}
    for name, fn in CHECKS.items():
        try:
            out[name] = bool(fn())
        except Exception:
            out[name] = False
    return out


def _watch() -> None:
    while True:
        snap = health_snapshot()
        changes = []
        with _lock:
            for name, ok in snap.items():
                before = _health.get(name, True)  # assume ok at start: alert only when something breaks
                if ok != before:
                    changes.append(("✅" if ok else "🚨") + f" <b>{html.escape(name)}</b> " + ("is back" if ok else "is DOWN"))
                _health[name] = ok
        if changes:
            _owners_dm("🩺 <b>Bot health</b>\n<blockquote>" + "\n".join(changes) + "</blockquote>")
        time.sleep(HEALTH_EVERY)


def start_health_watch() -> None:
    threading.Thread(target=_watch, daemon=True, name="health-watch").start()
