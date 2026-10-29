"""Groups the bot is in, for broadcasts.

Telegram gives bots no list of their chats, so a group is remembered when the bot is
added or promoted there, or gets a message there; it is forgotten when the bot is
removed, or when a broadcast finds it can no longer write there.
Saved in the kv store as "groups" = {"<chat id>": {"title": ..., "ts": ...}}.
"""
from __future__ import annotations

import threading
import time

from COMMANDS.ui_kit import state_get, state_set
from HELPERS.logger import logger

_lock = threading.Lock()
_known = set()          # ids already saved in this process (no write per message)


def all_groups() -> list:
    try:
        return sorted(int(k) for k in (state_get("groups", {}) or {}))
    except Exception as exc:
        logger.warning(f"[groups] read failed: {exc}")
        return []


def remember(chat) -> None:
    cid = getattr(chat, "id", None)
    if not isinstance(cid, int) or cid >= 0 or cid in _known:
        return
    with _lock:
        try:
            groups = dict(state_get("groups", {}) or {})
            groups[str(cid)] = {"title": (getattr(chat, "title", "") or "")[:100], "ts": int(time.time())}
            state_set("groups", groups)
            _known.add(cid)
        except Exception as exc:
            logger.warning(f"[groups] save {cid} failed: {exc}")


def forget(cid) -> None:
    with _lock:
        _known.discard(cid)
        try:
            groups = dict(state_get("groups", {}) or {})
            if groups.pop(str(cid), None) is not None:
                state_set("groups", groups)
        except Exception as exc:
            logger.warning(f"[groups] forget {cid} failed: {exc}")
