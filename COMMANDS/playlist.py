"""YouTube playlists: one card (count + quality), then the videos one by one.

A youtube.com/playlist?list=… link gets a card with the playlist name and size. The person picks parts of
10 (1–10, 11–20 …, at most MAX_PLAYLIST_COUNT videos at once) and qualities, taps Start, and every video goes through
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
BATCH = 10            # the playlist is offered in parts of 10 videos: 1–10, 11–20 …
PAGE_BATCHES = 12     # parts per card page (3 rows of 4)
READ_MAX = 500        # videos read from a playlist
_JOBS = {}            # token -> {"msg", "title", "urls", "sel" (chosen parts), "page", "qs" (chosen qualities), "ts"}
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
            "playlistend": READ_MAX, "socket_timeout": 20}
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


def _batches(job) -> list:
    n = len(job["urls"])
    return [(a, min(n, a + BATCH)) for a in range(0, n, BATCH)]


def chosen_urls(job) -> list:
    """Videos of the chosen parts, in playlist order."""
    b = _batches(job)
    return [u for k in sorted(job["sel"]) if k < len(b) for u in job["urls"][b[k][0]:b[k][1]]]


def _all_parts(job) -> list:
    """'All': every part, or as many from the start as fit in one run (max_count videos)."""
    out, n = [], 0
    for k, (a, b) in enumerate(_batches(job)):
        if n + (b - a) > max_count():
            break
        out.append(k)
        n += b - a
    return out


def _card(job):
    batches, chosen = _batches(job), chosen_urls(job)
    pages = max(1, -(-len(batches) // PAGE_BATCHES))
    page = max(0, min(job.get("page", 0), pages - 1))
    parts = ", ".join(f"{batches[k][0] + 1}–{batches[k][1]}" for k in sorted(job["sel"]))
    text = (f"{em('play')} <b>{html.escape(job['title'][:150])}</b>\n"
            f"<blockquote>📃 <b>{job['total']}</b> videos" + (f" · {html.escape(job['uploader'][:60])}" if job["uploader"] else "")
            + (f"\nThe first {len(job['urls'])} are offered." if job["total"] > len(job["urls"]) else "")
            + (f"\n✅ Chosen: {parts} ({len(chosen)} videos)" if chosen else "") + "</blockquote>\n"
            f"{em('down')} <b>Pick the videos (max {max_count()} at once) and the qualities.</b>")
    tok = job["token"]

    def pick(selected, icon, label, cb):   # chosen = green with ✔, the rest plain
        return btn("ok" if selected else icon, label, cb=cb, style=GREEN if selected else PLAIN)
    rows, row = [], []
    for k in range(page * PAGE_BATCHES, min(len(batches), (page + 1) * PAGE_BATCHES)):
        a, b = batches[k]
        row.append(pick(k in job["sel"], "down", f"{a + 1}–{b}", f"pl|{tok}|b|{k}"))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    every = _all_parts(job)
    all_label = f"All {len(job['urls'])}" if len(every) == len(batches) else f"First {max_count()}"
    rows.append([pick(job["sel"] and sorted(job["sel"]) == every, "down", all_label, f"pl|{tok}|all")])
    if pages > 1:
        rows.append([btn("back", "Prev", cb=f"pl|{tok}|p|{(page - 1) % pages}", style=PLAIN),
                     btn("memo", f"{page + 1}/{pages}", cb=f"pl|{tok}|p|{page}", style=PLAIN),
                     btn("next", "Next", cb=f"pl|{tok}|p|{(page + 1) % pages}", style=PLAIN)])
    qs = [pick(k in job["qs"], "music" if k == "a" else "play", label, f"pl|{tok}|q|{k}") for k, label in QUALITIES]
    rows += [qs[:3], qs[3:]]   # 3 + 3, side by side
    times = f" × {len(job['qs'])}" if len(job["qs"]) > 1 else ""   # several qualities: every video in each
    what = f" · {len(chosen)} videos{times}" if chosen else ""
    rows.append([btn("bolt", f"Start{what}", cb=f"pl|{tok}|go", style=GREEN),
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
    job = {"token": token, "msg": message, "sel": [], "page": 0, "qs": [q], "ts": time.time(), **data}   # only the quality is preset
    with _lock:
        for k in [k for k, v in _JOBS.items() if time.time() - v["ts"] > TTL]:
            _JOBS.pop(k, None)
        _JOBS[token] = job
    edit(client, wait, lambda: _card(job))


def stop(chat_id) -> None:
    with _lock:
        _STOP.add(int(chat_id))


def _status(client, chat, old, text, kb):
    """The status card stays at the bottom: videos arrive below it, so it is sent again (old one removed)."""
    try:
        new = send(client, chat, lambda: (text, kb))
        if old is not None:
            old.delete()
        return new or old
    except Exception:
        return old


def _next_job(job):
    """The next part after the chosen ones (same qualities), for one-tap 'Next 11–20'."""
    b = _batches(job)
    k = max(job["sel"]) + 1 if job["sel"] else 0
    if k >= len(b):
        return None
    token = secrets.token_hex(4)
    nxt = {**job, "token": token, "sel": [k], "page": k // PAGE_BATCHES, "qs": list(job["qs"]), "ts": time.time()}
    with _lock:
        _JOBS[token] = nxt
    return nxt


def _run(client, job, card) -> None:
    from COMMANDS.simple_ui import _run_choice
    msg = job["msg"]
    target = getattr(msg, "_dm_twin", None) or msg
    if target is not msg:
        target._playlist = True   # group request: the DM twin runs the videos
    chat = target.chat.id
    with _lock:
        _STOP.discard(chat)
    urls = chosen_urls(job)
    done = 0
    stop_kb = InlineKeyboardMarkup([[btn("ban", "Stop Playlist", cb=f"pl|{job['token']}|stop", style=RED)]])
    title = html.escape(job["title"][:120])
    status = None
    try:
        card.delete()   # the choice card goes; the status card follows the videos
    except Exception:
        pass
    for i, url in enumerate(urls, 1):
        with _lock:
            if chat in _STOP:
                break
        status = _status(client, chat, status, f"{em('play')} <b>{title}</b>\n<blockquote>📃 Video <b>{i}</b> of "
                                               f"<b>{len(urls)}</b> · {done} done</blockquote>", stop_kb)
        for q in job["qs"]:   # each chosen quality of this video, one after the other
            with _lock:
                if chat in _STOP:
                    break
            m = copy.copy(msg)
            m.text = url
            m._ui_title = None
            m._playlist = True
            try:
                _run_choice(client, m, q)   # waits until this video is sent (or failed)
            except Exception as exc:
                logger.info(f"[playlist] video {i} ({q}): {exc}")
        done += 1
    with _lock:
        stopped = chat in _STOP
        _STOP.discard(chat)
    nxt = None if stopped else _next_job(job)
    head = "⏹ <b>Playlist stopped.</b>" if stopped else f"{em('ok')} <b>Playlist part done.</b>"
    rows = []
    if nxt:
        a, b = _batches(nxt)[nxt["sel"][0]]
        rows.append([btn("next", f"Next {a + 1}–{b}", cb=f"pl|{nxt['token']}|go", style=GREEN),
                     btn("down", "Choose Parts", cb=f"pl|{nxt['token']}|show", style=PLAIN)])
    _status(client, chat, status, f"{head}\n<blockquote>{title}\n{done} of {len(urls)} videos handled"
                                  + (f"\nNext: {_batches(nxt)[nxt['sel'][0]][0] + 1}–{_batches(nxt)[nxt['sel'][0]][1]}"
                                     f" of {len(job['urls'])}" if nxt else "") + "</blockquote>",
            InlineKeyboardMarkup(rows) if rows else None)


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
        if action == "b" and len(parts) == 4 and parts[3].isdigit():   # a part (1–10, 11–20 …): tap / tap again
            k = int(parts[3])
            if k in job["sel"]:
                job["sel"].remove(k)
            elif k < len(_batches(job)):
                b = _batches(job)[k]
                if len(chosen_urls(job)) + (b[1] - b[0]) > max_count():
                    cq.answer(f"Max {max_count()} videos at once. Untick a part first.", show_alert=True)
                    raise RuntimeError("over the limit")
                job["sel"].append(k)
            edit(client, cq.message, lambda: _card(job))
        elif action == "all":   # tap: every part (up to the limit) green; tap again: none
            every = _all_parts(job)
            job["sel"] = [] if sorted(job["sel"]) == every else list(every)
            edit(client, cq.message, lambda: _card(job))
        elif action == "show":   # "Choose Parts" on the done card
            edit(client, cq.message, lambda: _card(job))
        elif action == "p" and len(parts) == 4 and parts[3].isdigit():
            job["page"] = int(parts[3])
            edit(client, cq.message, lambda: _card(job))
        elif action == "q" and len(parts) == 4 and parts[3] in dict(QUALITIES):
            k = parts[3]   # multi-select: tap = green, tap again = plain
            if k in job["qs"]:
                job["qs"].remove(k)
            else:
                job["qs"].append(k)
            edit(client, cq.message, lambda: _card(job))
        elif action == "go":
            if token not in _JOBS:
                raise RuntimeError("already started")
            if not job["sel"] or not job["qs"]:
                cq.answer("Choose the videos first (1–10, 11–20 …)." if not job["sel"] else "Choose at least one quality.",
                          show_alert=True)
                raise RuntimeError("nothing chosen")
            with _lock:
                _JOBS.pop(token, None)   # one run per card
                _JOBS[f"run-{token}"] = job
            from HELPERS.rate_limiter import check_rate_limit
            from COMMANDS.simple_ui import _is_admin
            ok, why = check_rate_limit(cq.from_user.id, _is_admin(cq.from_user.id))   # the playlist counts once
            if not ok:
                with _lock:
                    _JOBS[token] = job
                    _JOBS.pop(f"run-{token}", None)
                cq.answer(why or "Rate limit exceeded. Please wait.", show_alert=True)
                raise RuntimeError("rate limited")
            cq.answer(f"Starting {len(chosen_urls(job))} videos")
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
