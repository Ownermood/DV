"""YouTube playlists: one card (count + quality), then the videos one by one.

A youtube.com/playlist?list=… link gets a card with the playlist name and size. The person picks how many
(first 10 / 25 / all, at most MAX_PLAYLIST_COUNT) and the quality, taps Start, and every video goes through
the normal single-video pipeline (_run_choice), so limits, Cancel, groups (DM twin) and logs work as usual.
A status card shows "Video i of N"; Cancel on a video card or Stop on the status card ends the playlist.
"""
from __future__ import annotations

import copy
import html
import secrets
import threading
import time
from urllib.parse import parse_qs, urlparse

from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup

from CONFIG.limits import LimitsConfig
from pyrogram import enums
from COMMANDS.ui_kit import btn, em, send, edit, GREEN, RED
from HELPERS.app_instance import get_app
from HELPERS.logger import logger

app = get_app()
PLAIN = enums.ButtonStyle.DEFAULT
QUALITIES = (("b", "Best"), ("1080", "1080p"), ("720", "720p"), ("480", "480p"), ("360", "360p"), ("a", "MP3"))
COUNTS = (10, 25)
_JOBS = {}            # token -> {"msg", "title", "urls", "count", "q", "ts"}
_STOP = set()         # chats whose running playlist must stop
_lock = threading.Lock()
TTL = 3600


def is_playlist_url(url) -> bool:
    """A whole playlist (youtube.com/playlist?list=…); a watch?v=…&list=… link stays one video."""
    p = urlparse(url or "")
    host = (p.hostname or "").lower()
    return ("youtube.com" in host and p.path.rstrip("/") == "/playlist"
            and bool(parse_qs(p.query).get("list")))


def max_count() -> int:
    return int(getattr(LimitsConfig, "MAX_PLAYLIST_COUNT", 50) or 50)


def _read(url) -> dict:
    import yt_dlp
    from HELPERS.pot_helper import add_pot_to_ytdl_opts
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "extract_flat": "in_playlist",
            "playlistend": max_count(), "socket_timeout": 20}
    try:
        from COMMANDS import cookie_store
        ck = cookie_store.first_active("youtube")
        if ck:
            opts["cookiefile"] = ck
    except Exception:
        pass
    with yt_dlp.YoutubeDL(add_pot_to_ytdl_opts(opts, url)) as ydl:
        info = ydl.extract_info(url, download=False) or {}
    urls = []
    for e in info.get("entries") or []:
        if e and (e.get("id") or e.get("url")):
            urls.append(e.get("url") if str(e.get("url", "")).startswith("http")
                        else f"https://www.youtube.com/watch?v={e['id']}")
    return {"title": info.get("title") or "Playlist", "uploader": info.get("uploader") or info.get("channel") or "",
            "total": int(info.get("playlist_count") or len(urls)), "urls": urls}


def _card(job):
    n = len(job["urls"])
    text = (f"{em('play')} <b>{html.escape(job['title'][:150])}</b>\n"
            f"<blockquote>📃 <b>{job['total']}</b> videos" + (f" · {html.escape(job['uploader'][:60])}" if job["uploader"] else "")
            + (f"\nUp to {max_count()} per playlist." if job["total"] > n else "") + "</blockquote>\n"
            f"{em('down')} <b>How many and which quality?</b>")
    tok = job["token"]
    counts = [c for c in COUNTS if c < n] + [n]

    def pick(selected, icon, label, cb):   # chosen = ✔ icon, every option button plain (colour only on Start / Close)
        return btn("ok" if selected else icon, label, cb=cb, style=PLAIN)
    rows = [[pick(job["count"] == c, "down", f"{'All ' if c == n else 'First '}{c}", f"pl|{tok}|c|{c}") for c in counts]]
    qs = [pick(job["q"] == k, "music" if k == "a" else "play", label, f"pl|{tok}|q|{k}") for k, label in QUALITIES]
    rows += [qs[:3], qs[3:]]   # 3 + 3, side by side
    rows.append([btn("bolt", f"Start · {job['count']} videos", cb=f"pl|{tok}|go", style=GREEN),
                 btn("trash", "Close", cb=f"pl|{tok}|x", style=RED)])
    return text, InlineKeyboardMarkup(rows)


def show(client, message, url) -> None:
    """The playlist card (reads the list first: ~2 s)."""
    from COMMANDS.simple_ui import t
    chat = message.chat.id
    wait = send(client, chat, lambda: (t(chat, "loading"), None), reply_to_message_id=message.id)
    try:
        data = _read(url)
    except Exception as exc:
        logger.info(f"[playlist] {url}: {exc}")
        data = {"urls": []}
    if not data["urls"]:
        edit(client, wait, lambda: (f"{em('warn')} <b>This playlist is empty, private or unavailable.</b>", None))
        return
    try:
        from COMMANDS.user_prefs import quality, person_of
        q = quality(person_of(message), "youtube") or "b"
    except Exception:
        q = "b"
    token = secrets.token_hex(4)
    job = {"token": token, "msg": message, "count": len(data["urls"]), "q": q, "ts": time.time(), **data}
    with _lock:
        for k in [k for k, v in _JOBS.items() if time.time() - v["ts"] > TTL]:
            _JOBS.pop(k, None)
        _JOBS[token] = job
    edit(client, wait, lambda: _card(job))


def stop(chat_id) -> None:
    with _lock:
        _STOP.add(int(chat_id))


def _run(client, job, card) -> None:
    from COMMANDS.simple_ui import _run_choice
    msg = job["msg"]
    target = getattr(msg, "_dm_twin", None) or msg
    chat = target.chat.id
    with _lock:
        _STOP.discard(chat)
    urls = job["urls"][:job["count"]]
    done = failed = 0
    stop_kb = InlineKeyboardMarkup([[btn("ban", "Stop Playlist", cb=f"pl|{job['token']}|stop", style=RED)]])
    for i, url in enumerate(urls, 1):
        with _lock:
            if chat in _STOP:
                break
        try:
            edit(client, card, lambda i=i: (f"{em('play')} <b>{html.escape(job['title'][:120])}</b>\n"
                                            f"<blockquote>📃 Video <b>{i}</b> of <b>{len(urls)}</b>"
                                            f" · ✅ {done} sent" + (f" · ⚠️ {failed} failed" if failed else "")
                                            + "</blockquote>", stop_kb))
        except Exception:
            pass
        m = copy.copy(msg)
        m.text = url
        m._ui_title = None
        try:
            _run_choice(client, m, job["q"])   # waits until this video is sent (or failed)
            done += 1
        except Exception as exc:
            failed += 1
            logger.info(f"[playlist] video {i}: {exc}")
    with _lock:
        stopped = chat in _STOP
        _STOP.discard(chat)
    head = "⏹ <b>Playlist stopped.</b>" if stopped else f"{em('ok')} <b>Playlist done.</b>"
    try:
        edit(client, card, lambda: (f"{head}\n<blockquote>{html.escape(job['title'][:120])}\n"
                                    f"{done} of {len(urls)} videos handled</blockquote>", None))
    except Exception:
        pass


@app.on_callback_query(filters.regex(r"^pl\|"), group=-1)
def playlist_buttons(client, cq):
    parts = cq.data.split("|")
    token, action = parts[1], parts[2] if len(parts) > 2 else ""
    with _lock:
        job = _JOBS.get(token) or _JOBS.get(f"run-{token}")   # a running playlist keeps its Stop button
    from COMMANDS.simple_ui import _owner_of
    if not job or cq.from_user.id != _owner_of(job["msg"]):
        try:
            cq.answer("This menu expired or is not yours.", show_alert=True)
        finally:
            cq.stop_propagation()
    try:
        if action == "c" and len(parts) == 4 and parts[3].isdigit():
            job["count"] = max(1, min(int(parts[3]), len(job["urls"])))
            edit(client, cq.message, lambda: _card(job))
        elif action == "q" and len(parts) == 4 and parts[3] in dict(QUALITIES):
            job["q"] = parts[3]
            edit(client, cq.message, lambda: _card(job))
        elif action == "go":
            if token not in _JOBS:
                raise RuntimeError("already started")
            with _lock:
                _JOBS.pop(token, None)   # one run per card
                _JOBS[f"run-{token}"] = job
            cq.answer(f"Starting {job['count']} videos")
            threading.Thread(target=_run, args=(client, job, cq.message), daemon=True).start()
        elif action == "stop":
            target = getattr(job["msg"], "_dm_twin", None) or job["msg"]
            stop(target.chat.id)
            cq.answer("Stopping after this video")
        elif action == "x":
            with _lock:
                _JOBS.pop(token, None)
            cq.message.delete()
        cq.answer()
    except Exception as exc:
        if "MESSAGE_NOT_MODIFIED" not in str(exc) and "QUERY_ID_INVALID" not in str(exc):
            logger.info(f"[playlist] button {cq.data}: {exc}")
    cq.stop_propagation()
