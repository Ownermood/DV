"""User activity and download counters for the admin stats.

Kept in memory and written to CONFIG/.activity.json at most every 30 s
(atomic replace). Tracking starts with this update: older users have no
activity yet.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from HELPERS.logger import logger

IST = timezone(timedelta(hours=5, minutes=30))
_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "CONFIG", ".activity.json")
_FLUSH_EVERY = 30
_KEEP_DAYS = 60

_lock = threading.RLock()
_data = None          # {"seen": {uid: last_ts}, "dl": {"YYYY-MM-DD": n}, "dl_total": n, "since": ts}
_dirty = False
_last_flush = 0.0


def _load():
    global _data
    if _data is None:
        try:
            with open(_FILE, "r", encoding="utf-8") as f:
                _data = json.load(f)
        except FileNotFoundError:
            _data = {}
        except Exception as exc:
            logger.warning(f"[activity] unreadable, starting fresh: {exc}")
            _data = {}
        _data.setdefault("seen", {})
        _data.setdefault("dl", {})
        _data.setdefault("dl_total", 0)
        _data.setdefault("since", int(time.time()))
    return _data


def _flush(force=False):
    global _dirty, _last_flush
    if not _dirty or (not force and time.time() - _last_flush < _FLUSH_EVERY):
        return
    try:
        tmp = _FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_data, f)
        os.replace(tmp, _FILE)
        _dirty = False
        _last_flush = time.time()
    except Exception as exc:
        logger.warning(f"[activity] save failed: {exc}")


def _today() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


def touch(user_id) -> None:
    """User did something (message or button)."""
    global _dirty
    with _lock:
        _load()["seen"][str(int(user_id))] = int(time.time())
        _dirty = True
        _flush()


def download(user_id) -> None:
    """User started a download (video, MP3 or thumbnail)."""
    global _dirty
    with _lock:
        d = _load()
        day = _today()
        d["dl"][day] = d["dl"].get(day, 0) + 1
        d["dl_total"] = d.get("dl_total", 0) + 1
        cutoff = (datetime.now(IST) - timedelta(days=_KEEP_DAYS)).strftime("%Y-%m-%d")
        for k in [k for k in d["dl"] if k < cutoff]:
            d["dl"].pop(k, None)
        _dirty = True
        _flush()
    touch(user_id)


def snapshot() -> dict:
    """Active users (24h/7d/30d), downloads (today/7d/30d/all), tracking start."""
    now = time.time()
    with _lock:
        d = _load()
        seen = list(d["seen"].values())
        days = dict(d["dl"])
        total = d.get("dl_total", 0)
        since = d.get("since")
    today = datetime.now(IST).date()

    def dl_last(n):
        return sum(v for k, v in days.items() if (today - datetime.strptime(k, "%Y-%m-%d").date()).days < n)
    return {
        "active": [sum(1 for t in seen if now - t <= s) for s in (86400, 7 * 86400, 30 * 86400)],
        "tracked": len(seen),
        "dl": [days.get(_today(), 0), dl_last(7), dl_last(30), total],
        "since": since,
    }


def flush_now() -> None:
    with _lock:
        _flush(force=True)


def _ticker():
    while True:
        time.sleep(_FLUSH_EVERY)
        with _lock:
            _flush()


threading.Thread(target=_ticker, daemon=True, name="activity-flush").start()
