"""Front door of the bot: welcome screen, link auto-detect, clean command set.

Supported: YouTube, Instagram, Facebook, Snapchat, Pinterest, TeraBox (nothing else; TikTok is banned in India).
Registered at handler group -1 so it runs before every legacy handler.
"""
from __future__ import annotations

import html
import os
import re
import secrets
import threading
import time
from typing import Optional, Tuple
from urllib.parse import urlparse

from pyrogram import filters, enums
from pyrogram.types import (InlineKeyboardButton, InlineKeyboardMarkup,
                            ReplyKeyboardRemove, BotCommand)

from CONFIG.config import Config
from HELPERS.app_instance import get_app
from HELPERS.logger import logger
from COMMANDS import activity
from COMMANDS.ui_kit import btn, em, send, edit, t, BLUE, GREEN, RED

app = get_app()

# key -> (label, emoji, domain suffixes)
# TeraBox and all its mirror / share domains (same list as ADAPTERS/terabox.py)
TERABOX_DOMAINS = ("terabox.com", "terabox.app", "terabox.fun", "terabox.best", "terabox.club", "terabox.click", "terabox.me", "terabox.site", "terabox.pro", "terabox.xyz", "teraboxapp.com", "teraboxlink.com", "teraboxlinke.com", "teraboxshare.com", "teraboxsharefile.com", "teraboxurl.com", "teraboxfree.com", "teraboxmod.app", "terasharelink.com", "terasharefile.com", "terashareus.com", "terafileshare.com", "terasharedrive.com", "tera1024box.com", "1024tera.com", "1024tera.co", "1024terabox.com", "1024-terabox.com", "1024box.com", "4funbox.com", "4funbox.co", "4funbox.in", "mirrobox.com", "nephobox.com", "freeterabox.com", "momerybox.com", "tibibox.com", "gibibox.com", "pebibox.com", "fancybox.in", "dubox.com", "bestclouddrive.com", "playduo.link")

PLATFORMS = {
    "youtube":   ("YouTube",   "▶️", ("youtube.com", "youtu.be", "youtube-nocookie.com")),
    "instagram": ("Instagram", "📸", ("instagram.com", "instagr.am")),
    "facebook":  ("Facebook",  "📘", ("facebook.com", "fb.watch", "fb.com")),
    "snapchat":  ("Snapchat",  "👻", ("snapchat.com", "snap.com")),
    "pinterest": ("Pinterest", "📌", ("pinterest.com", "pin.it", "pinterest.co.uk", "pinterest.fr", "pinterest.de", "pinterest.ca", "pinterest.com.au", "pinterest.ph", "pinterest.es", "pinterest.it")),
    "terabox":   ("TeraBox",   "📦", TERABOX_DOMAINS),
}

ALLOWED_USER_COMMANDS = {"start", "help"}   # cookies are the admin's job (Cookie Manager)

BOT_MENU = [
    ("start", "Start"),
    ("help", "What this bot can do"),
]

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)


def extract_url(text: str) -> Optional[str]:
    m = _URL_RE.search(text or "")
    return m.group(0).rstrip(").,]") if m else None


def detect_platform(text: str) -> Optional[Tuple[str, str, str, str]]:
    """Return (key, label, emoji, url) for a supported link, else None."""
    url = extract_url(text)
    if not url:
        return None
    try:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return None
    for key, (label, emoji, suffixes) in PLATFORMS.items():
        if any(host == s or host.endswith("." + s) for s in suffixes):
            return key, label, emoji, url
    return None


def welcome_text(first_name: str = "", user_id=None) -> str:
    name = html.escape(first_name.strip()) if first_name else "there"
    return t(user_id, "welcome", name=name, max_gb=_max_gb(), platforms=_platform_lines())


# Premium logos (custom emoji IDs) for every platform
PLATFORM_EMOJI = {"youtube": "5355235592844095825", "instagram": "5355097780228470775",
                  "facebook": "5355254460635428635",
                  "snapchat": "5352719553757466112", "pinterest": "5352595631066071365",
                  "terabox": "5976766782763962762"}


def platform_icon(key) -> str:
    from COMMANDS.ui_kit import premium_on
    label, char, _ = PLATFORMS[key]
    cid = PLATFORM_EMOJI.get(key)
    return f'<emoji id="{cid}">{char}</emoji>' if cid and premium_on() else char


def _platform_lines() -> str:
    return "\n".join(f"{platform_icon(k)} {v[0]}" for k, v in PLATFORMS.items())


def unsupported_text(user_id=None) -> str:
    return t(user_id, "unsupported") + "\n\n<blockquote>" + _platform_lines() + "</blockquote>"


def help_text(user_id=None) -> str:
    return t(user_id, "help_text", max_gb=_max_gb(), platforms=_platform_lines())


def _max_gb() -> str:
    return f"{float(getattr(Config, 'MAX_FILE_SIZE_GB', 4)):g}"


def format_for(height: Optional[int] = None) -> str:
    """Best quality under MAX_FILE_SIZE_GB; yt-dlp falls to the next lower
    resolution automatically when the bigger one exceeds the size cap.
    Unknown sizes pass (`<?`); the downloader's own size check is the backstop."""
    cap = int(float(getattr(Config, "MAX_FILE_SIZE_GB", 4)) * 1024 * 0.95)  # 5% headroom for audio
    h = f"[height<={int(height)}]" if height else ""
    size = f"[filesize<?{cap}M][filesize_approx<?{cap}M]"
    return f"bv*{h}{size}+ba/b{h}{size}/b{h}"


def _is_admin(user_id: int) -> bool:
    try:
        return int(user_id) in Config.ADMIN
    except Exception:
        return False


def _start_markup(user_id) -> InlineKeyboardMarkup:
    rows = [[btn("question", t(user_id, "help"), cb="ui|help", style=GREEN)]]
    if _is_admin(user_id):
        rows.append([btn("crown", t(user_id, "admin"), cb="ap|home", style=RED)])
    return InlineKeyboardMarkup(rows)


def _back_markup(user_id, cb="ui|home") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[btn("back", t(user_id, "back"), cb=cb, style=BLUE)]])


def _start_image():
    from COMMANDS.ui_kit import state_get
    return state_get("start_image") or getattr(Config, "START_IMAGE", "") or None


def send_start(client, chat_id, first_name=""):
    send(client, chat_id, lambda: (welcome_text(first_name, chat_id), _start_markup(chat_id)), photo=_start_image())


_menu_set = False


def _ensure_menu(client) -> None:
    global _menu_set
    if _menu_set:
        return
    _menu_set = True
    # Wider scopes beat the default one in Telegram's menu priority: clear any old lists there
    # (BotFather / an older bot) so users only get the public menu; admins get theirs per chat.
    from pyrogram.types import (BotCommandScopeAllPrivateChats, BotCommandScopeAllGroupChats,
                                BotCommandScopeAllChatAdministrators)
    for scope in (BotCommandScopeAllPrivateChats(), BotCommandScopeAllGroupChats(),
                  BotCommandScopeAllChatAdministrators()):
        try:
            client.delete_bot_commands(scope=scope)
        except Exception as exc:
            logger.info(f"[ui] clearing {type(scope).__name__} menu: {exc}")
    try:
        client.set_bot_commands([BotCommand(c, d) for c, d in BOT_MENU])
    except Exception as exc:
        logger.warning(f"[ui] set_bot_commands failed: {exc}")
    from COMMANDS.admin_panel import ensure_admin_menus
    ensure_admin_menus(client)


_registered = set()


def _register_user(message) -> None:
    """Persist the user once per process so broadcasts reach them."""
    uid = message.chat.id
    if uid in _registered:
        return
    try:
        from DATABASE.firebase_init import check_user
        check_user(message)
        _registered.add(uid)
    except Exception as exc:
        logger.warning(f"[ui] user register failed for {uid}: {exc}")


# ---------------- link menu ----------------
_PENDING = {}  # token -> (message, platform_key, ts)
_PENDING_TTL = 3600
_pending_lock = threading.Lock()
_SIZES = {}  # token -> _yt_pick() result from the YouTube probe
_AFTER = {}  # token -> (message, ts): MP3 / Thumbnail buttons under a sent video
_AFTER_TTL = 24 * 3600
YT_HEIGHTS = (2160, 1440, 1080, 720, 480, 360, 240, 144)


def _remember(message, key) -> str:
    token = secrets.token_hex(4)
    now = time.time()
    with _pending_lock:
        for k in [k for k, v in _PENDING.items() if now - v[2] > _PENDING_TTL]:
            _PENDING.pop(k, None)
            _SIZES.pop(k, None)
        _PENDING[token] = (message, key, now)
    return token


def _human(n) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit in ("B", "KB") else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def _ydl_opts(cookie: Optional[str] = None) -> dict:
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True,
            "socket_timeout": 20}
    if cookie:
        opts["cookiefile"] = cookie
    return opts


def _probe(url, user_id) -> Optional[dict]:
    """No cookie first (a stale cookie itself triggers YouTube's bot check), then the Cookie Manager cookie."""
    import yt_dlp
    from COMMANDS import cookie_store
    from COMMANDS.cookies_cmd import shared_cookie_path
    plat = detect_platform(url)
    shared = cookie_store.first_active("youtube") if plat and plat[0] == "youtube" else shared_cookie_path(url)
    for cookie in dict.fromkeys([None, shared]):
        try:
            from HELPERS.proxy_helper import youtube_proxy
            opts = _ydl_opts(cookie)
            if youtube_proxy(url):
                opts["proxy"] = youtube_proxy(url)
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            if info and info.get("_type") == "playlist":
                entries = [e for e in (info.get("entries") or []) if e]
                info = entries[0] if entries else info
            return info
        except Exception as exc:
            logger.info(f"[ui] probe failed for {url} (cookie={'yes' if cookie else 'no'}): {exc}")
    return None


def _cap_bytes() -> float:
    return float(getattr(Config, "MAX_FILE_SIZE_GB", 4)) * 1024 ** 3


def _yt_pick(info) -> dict:
    """height -> (format_id or None, total bytes, muxed).

    Per height: the biggest (= best) format whose video + best audio stays within
    the limit. If none fits: (None, smallest total, False) so the menu can say why."""
    fmts = info.get("formats") or []
    size = lambda f: f.get("filesize") or f.get("filesize_approx") or 0
    audio = max((size(f) for f in fmts if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")), default=0)
    cands = {}
    for f in fmts:
        hgt = f.get("height")
        if not hgt or f.get("vcodec") in (None, "none") or not size(f) or not f.get("format_id"):
            continue
        for target in YT_HEIGHTS:
            if abs(hgt - target) <= target * 0.1:  # 1080 covers 1072 etc.
                muxed = f.get("acodec") not in (None, "none")
                cands.setdefault(target, []).append((f["format_id"], size(f) + (0 if muxed else audio), muxed))
    cap = _cap_bytes()
    out = {}
    for hgt, items in cands.items():
        fit = [c for c in items if c[1] <= cap]
        out[hgt] = max(fit, key=lambda c: c[1]) if fit else (None, min(c[1] for c in items), False)
    return out


def _yt_sizes(info) -> dict:
    """height -> bytes of the format that would be downloaded (see _yt_pick)."""
    return {h: p[1] for h, p in _yt_pick(info).items()}


def _choice_rows(uid, token, yt_sizes=None, is_yt=False):
    rows = []
    if is_yt:
        heights = sorted(yt_sizes, reverse=True) if yt_sizes else list(YT_HEIGHTS[:6])
        cap = float(getattr(Config, "MAX_FILE_SIZE_GB", 4)) * 1024 ** 3
        row = []
        for hgt in heights:
            sz = (yt_sizes or {}).get(hgt)
            label = {2160: "4K", 1440: "2K"}.get(hgt, f"{hgt}p") + (f" • {_human(sz)}" if sz else "")
            # plain (uncoloured) quality buttons with the size; ⛔ = over the limit
            row.append(btn("play", label + (" ⛔" if sz and sz > cap else ""), cb=f"ui|g|{token}|{hgt}",
                           style=enums.ButtonStyle.DEFAULT))
            if len(row) == 3:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
    plain = enums.ButtonStyle.DEFAULT
    rows.append([btn("bolt", t(uid, "best"), cb=f"ui|g|{token}|b", style=plain)])
    rows.append([btn("music", t(uid, "mp3"), cb=f"ui|g|{token}|a", style=plain),
                 btn("image", t(uid, "thumb"), cb=f"ui|g|{token}|t", style=plain)])
    rows.append([btn("trash", t(uid, "close"), cb="ui|close", style=plain)])
    return InlineKeyboardMarkup(rows)


def _count(n) -> str:
    n = float(n or 0)
    for unit in ("", "K", "M", "B"):
        if n < 1000:
            return f"{n:.0f}{unit}" if unit == "" else f"{n:.1f}{unit}".replace(".0", "")
        n /= 1000
    return f"{n:.1f}T"


_YT_ID = re.compile(r"(?:v=|youtu\.be/|shorts/|live/|embed/)([\w-]{11})")


def _oembed(url) -> Optional[dict]:
    """Basic info from YouTube's public oEmbed (no bot check): id, title, channel."""
    m = _YT_ID.search(url or "")
    if not m:
        return None
    try:
        import requests
        r = requests.get("https://www.youtube.com/oembed", params={"url": f"https://www.youtube.com/watch?v={m.group(1)}",
                                                                  "format": "json"}, timeout=10)
        d = r.json() if r.ok else {}
        return {"id": m.group(1), "title": d.get("title"), "channel": d.get("author_name")}
    except Exception as exc:
        logger.info(f"[ui] oembed failed: {exc}")
        return {"id": m.group(1)}


def _yt_preview(info, picks) -> str:
    """Caption: title, date / length / views / comments / channel, then every quality with its size."""
    title = html.escape(info.get("title") or "YouTube")[:180]
    meta = []
    d = str(info.get("upload_date") or "")
    if len(d) == 8:
        meta.append(f"{em('memo')} {d[6:]}-{d[4:6]}-{d[:4]}")
    dur = int(info.get("duration") or 0)
    if dur:
        meta.append(f"{em('play')} {dur // 3600}:{dur % 3600 // 60:02d}:{dur % 60:02d}" if dur >= 3600
                    else f"{em('play')} {dur // 60}:{dur % 60:02d}")
    if info.get("view_count"):
        meta.append(f"{em('fire')} {_count(info['view_count'])} views")
    if info.get("comment_count"):
        meta.append(f"{em('chat')} {_count(info['comment_count'])}")
    who = html.escape(info.get("channel") or info.get("uploader") or "")[:60]
    if who:
        meta.append(f"{em('crown')} {who}" + (f" • {_count(info['channel_follower_count'])} subs"
                                              if info.get("channel_follower_count") else ""))
    lines = [f"{em('play')} <b>{title}</b>", "<blockquote>" + "\n".join(meta) + "</blockquote>" if meta else ""]
    if picks:
        cap = _cap_bytes()
        q = []
        for i, hgt in enumerate(sorted(picks, reverse=True), 1):
            sz = picks[hgt][1]
            name = {2160: "4K", 1440: "2K"}.get(hgt, f"{hgt}p")
            q.append(f"{i}. {name} — {_human(sz) if sz else '?'}" + (" ⛔" if sz and sz > cap else ""))
        lines.append(f"{em('play')} <b>Video</b>\n<blockquote expandable>" + "\n".join(q) + "</blockquote>")
    fmts = info.get("formats") or []
    aud = max((f.get("filesize") or f.get("filesize_approx") or 0 for f in fmts
               if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")), default=0)
    if aud:
        lines.append(f"{em('music')} <b>Audio</b>\n<blockquote>MP3 — {_human(aud)}</blockquote>")
    lines.append(f"{em('down')} <b>Choose a quality (up to {_max_gb()} GB):</b>")
    return "\n".join(x for x in lines if x)[:1024]


def _show_link_menu(client, message, key, label) -> None:
    uid = message.chat.id
    token = _remember(message, key)
    if key != "youtube":
        send(client, uid, lambda: (t(uid, "pick", platform=f"{platform_icon(key)} {label}"), _choice_rows(uid, token)),
             reply_to_message_id=message.id)
        return
    menu = send(client, uid, lambda: (t(uid, "loading"), None), reply_to_message_id=message.id)

    def work():
        info = _probe(extract_url(message.text), uid)
        picks = _yt_pick(info) if info else None
        sizes = {h: p[1] for h, p in picks.items()} if picks else None
        if picks:
            _SIZES[token] = picks
        title = html.escape((info or {}).get("title") or "YouTube")[:200]
        if not info:  # yt-dlp blocked (server IP): title, channel and thumbnail from oEmbed still make a preview
            info = _oembed(extract_url(message.text))
        if info and info.get("id"):
            try:  # preview card: thumbnail + details + sizes (like the big downloader bots)
                kb = _choice_rows(uid, token, sizes, is_yt=True)
                send(client, uid, lambda: (_yt_preview(info, picks), kb),
                     photo=f"https://i.ytimg.com/vi/{info['id']}/hqdefault.jpg", reply_to_message_id=message.id)
                menu.delete()
                return
            except Exception as exc:
                logger.warning(f"[ui] yt preview failed, text menu instead: {exc}")
        try:
            edit(client, menu, lambda: (t(uid, "pick_yt", title=title, max_gb=_max_gb()),
                                        _choice_rows(uid, token, sizes, is_yt=True)))
        except Exception as exc:
            logger.warning(f"[ui] yt menu edit failed: {exc}")
    threading.Thread(target=work, daemon=True).start()


def _too_big(token, choice):
    """(size, fitting heights) when the chosen YouTube quality is over the limit, else None."""
    picks = _SIZES.get(token)
    if not picks or choice in ("a", "t"):
        return None
    picked = max(picks) if choice == "b" else int(choice)
    if picked not in picks or picks[picked][0]:
        return None
    fits = sorted((h for h, p in picks.items() if p[0]), reverse=True)
    return picks[picked][1], fits


def _too_big_view(uid, token, size, fits):
    text = (f"{em('warn')} <b>Sorry, this quality is too large.</b>\n\n"
            f"<blockquote>This video is about <b>{_human(size)}</b> in the selected quality, "
            f"and the bot can send up to <b>{_max_gb()} GB</b>.\n"
            + ("Please choose one of the qualities below, they fit perfectly." if fits else
               "No video quality fits the limit, but you can still get the audio.") + "</blockquote>")
    rows, row = [], []
    for hgt in fits:
        label = {2160: "4K", 1440: "2K"}.get(hgt, f"{hgt}p") + f" • {_human(_SIZES[token][hgt][1])}"
        row.append(btn("play", label, cb=f"ui|g|{token}|{hgt}", style=enums.ButtonStyle.DEFAULT))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([btn("music", "MP3", cb=f"ui|g|{token}|a", style=enums.ButtonStyle.DEFAULT),
                 btn("trash", "Close", cb="ui|close", style=enums.ButtonStyle.DEFAULT)])
    return text, InlineKeyboardMarkup(rows)


def _after_markup(message) -> InlineKeyboardMarkup:
    """MP3 + Thumbnail buttons shown under the downloaded video."""
    token = secrets.token_hex(4)
    now = time.time()
    with _pending_lock:
        for k in [k for k, v in _AFTER.items() if now - v[1] > _AFTER_TTL]:
            _AFTER.pop(k, None)
        _AFTER[token] = (message, now)
    uid = message.chat.id
    return InlineKeyboardMarkup([[btn("music", t(uid, "mp3"), cb=f"ui|x|{token}|a", style=BLUE),
                                  btn("image", t(uid, "thumb"), cb=f"ui|x|{token}|t", style=BLUE)]])


def _run_choice(client, message, choice, picks=None) -> None:
    """Hand the stored link to the legacy pipeline (keeps ban/sub/rate checks)."""
    from URL_PARSERS.url_extractor import url_distractor
    original = message.text
    try:
        message._ui_format = None
        message._ui_after_kb = None if choice == "a" else _after_markup(message)
        if choice == "a":
            message.text = f"/audio {extract_url(original) or original}"
            message._ui_choice = None
        else:
            message._ui_choice = 0 if choice == "b" else int(choice)
            if picks:  # YouTube with known sizes: download exactly the format the menu checked
                hgt = max(h for h, p in picks.items() if p[0]) if choice == "b" else int(choice)
                fid, _, muxed = picks.get(hgt, (None, 0, False))
                if fid:
                    message._ui_choice = hgt
                    message._ui_format = (fid if muxed else f"{fid}+ba") + "/" + format_for(hgt)
        url_distractor(client, message)
    except Exception as exc:
        logger.error(f"[ui] choice {choice} failed: {exc}")
    finally:
        message.text = original


# ---------------- many links: one-by-one queue ----------------
MAX_QUEUE = 20
_queues = {}            # user id -> [(message copy, url), ...]
_queue_lock = threading.Lock()


def _queue_links(client, message, links) -> None:
    import copy
    uid = message.chat.id
    ok = [u for u in links if detect_platform(u)]
    bad = len(links) - len(ok)
    with _queue_lock:
        q = _queues.get(uid)
        running = q is not None
        q = q if running else []
        room = max(0, MAX_QUEUE - len(q))
        for u in ok[:room]:
            m = copy.copy(message)
            m.text = u
            q.append((m, u))
        _queues[uid] = q
        waiting = len(q)
    skipped = len(ok) - min(len(ok), room)
    lines = [f"{em('down')} <b>{min(len(ok), room)} link(s) added to your queue</b>",
             "<blockquote>" + "\n".join(f"{i}. {platform_icon(detect_platform(u)[0])} {html.escape(u[:60])}"
                                       for i, u in enumerate(ok[:room], 1)) + "</blockquote>" if ok[:room] else "",
             f"{em('bolt')} Best quality, one by one. In queue: <b>{waiting}</b>",
             f"{em('warn')} {bad} unsupported link(s) skipped." if bad else "",
             f"{em('warn')} Queue is full (max {MAX_QUEUE}), {skipped} link(s) skipped." if skipped else ""]
    send(client, uid, lambda: ("\n".join(x for x in lines if x), None), reply_to_message_id=message.id)
    if ok[:room] and not running:
        threading.Thread(target=_run_queue, args=(client, uid), daemon=True).start()


def _run_queue(client, uid) -> None:
    try:
        while True:
            with _queue_lock:
                q = _queues.get(uid) or []
                if not q:
                    _queues.pop(uid, None)
                    return
                m, url = q.pop(0)
            activity.download(uid)
            _run_choice(client, m, "b")   # waits until this link is sent
    except Exception as exc:
        logger.error(f"[ui] queue for {uid} stopped: {exc}")
        with _queue_lock:
            _queues.pop(uid, None)


def _send_thumbnail(client, message) -> None:
    from DATABASE.firebase_init import is_user_blocked
    from HELPERS.limitter import is_user_in_channel
    uid = message.chat.id
    if is_user_blocked(message) or (not _is_admin(uid) and not is_user_in_channel(client, message)):
        return
    url = extract_url(message.text)
    info = _probe(url, uid) or {}
    thumbs = sorted((x for x in info.get("thumbnails") or [] if x.get("url")),
                    key=lambda x: (x.get("preference") or 0, (x.get("width") or 0) * (x.get("height") or 0)))
    thumb = info.get("thumbnail") or (thumbs[-1]["url"] if thumbs else None)
    if not thumb:
        send(client, uid, lambda: (t(uid, "no_thumb"), None), reply_to_message_id=message.id)
        return
    title = html.escape(info.get("title") or "Thumbnail")[:300]
    cap = f'<blockquote expandable>{em("image")} <b>{title}</b>\n<a href="{html.escape(url)}">{em("globe")} Link</a></blockquote>'
    try:
        send(client, uid, lambda: (cap, None), photo=thumb, reply_to_message_id=message.id)
        return
    except Exception as exc:
        logger.info(f"[ui] thumb by URL failed, downloading: {exc}")
    import requests
    path = os.path.join("users", str(uid), f"thumb_{secrets.token_hex(4)}.jpg")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        r = requests.get(thumb, timeout=20)
        r.raise_for_status()
        if len(r.content) > 10 * 1024 * 1024:
            raise ValueError("thumbnail too large")
        with open(path, "wb") as f:
            f.write(r.content)
        # Telegram rejects webp as photo -> convert
        from PIL import Image
        with Image.open(path) as im:
            im.convert("RGB").save(path, "JPEG", quality=95)
        send(client, uid, lambda: (cap, None), photo=path, reply_to_message_id=message.id)
    except Exception as exc:
        logger.warning(f"[ui] thumbnail failed: {exc}")
        send(client, uid, lambda: (t(uid, "no_thumb"), None), reply_to_message_id=message.id)
    finally:
        if os.path.exists(path):
            os.remove(path)


@app.on_message(filters.private & filters.text, group=-1)
def front_door(client, message):
    _ensure_menu(client)
    _register_user(message)
    from HELPERS.decorators import clear_old_reply_keyboard
    clear_old_reply_keyboard(message.chat.id)   # the old /clean /cookie /settings keyboard
    activity.touch(message.chat.id)
    text = (message.text or "").strip()
    chat_id = message.chat.id
    admin = _is_admin(chat_id)

    if admin:
        from COMMANDS.admin_panel import handle_admin_input
        if handle_admin_input(client, message):
            message.stop_propagation()

    if text.startswith("/"):
        cmd = text[1:].split()[0].split("@")[0].lower()
        if cmd == "start":
            send_start(client, chat_id, message.from_user.first_name if message.from_user else "")
            message.stop_propagation()
        if cmd == "help":
            send(client, chat_id, lambda: (help_text(chat_id), _back_markup(chat_id)))
            message.stop_propagation()
        if cmd in ("admin", "panel") and admin:
            from COMMANDS.admin_panel import open_panel
            open_panel(client, chat_id)
            message.stop_propagation()
        if admin and cmd in ("broadcast", "delbc", "delbroadcast", "admins", "addadmin", "removeadmin"):
            from COMMANDS.admin_panel import handle_command
            if handle_command(client, message, cmd):
                message.stop_propagation()
        if cmd in ALLOWED_USER_COMMANDS or admin:
            return  # legacy handlers (cookies / admin) continue
        send(client, chat_id, lambda: (t(chat_id, "cmd_na"), None))
        message.stop_propagation()

    if not _URL_RE.search(text):
        if admin or text in ("",):
            return
        send(client, chat_id, lambda: (t(chat_id, "send_link"), None))
        message.stop_propagation()

    links = list(dict.fromkeys(m.group(0).rstrip(").,]") for m in _URL_RE.finditer(text)))
    if len(links) > 1 or chat_id in _queues:  # many links (or a queue running): queue, best quality, one by one
        _queue_links(client, message, links)
        message.stop_propagation()

    found = detect_platform(text)
    if found is None:
        send(client, chat_id, lambda: (unsupported_text(chat_id), None))
        message.stop_propagation()
    key, label, _, _ = found
    if key == "terabox":
        return  # TeraBox adapter is a direct download
    if key == "youtube":
        _show_link_menu(client, message, key, label)   # quality picker (+ MP3 / Thumbnail)
    else:
        # other sites: best quality straight away; MP3 / Thumbnail come as buttons under the video
        activity.download(chat_id)
        threading.Thread(target=_run_choice, args=(client, message, "b"), daemon=True).start()
    message.stop_propagation()


@app.on_callback_query(filters.regex(r"^ui\|"), group=-1)
def ui_callbacks(client, cq):
    parts = cq.data.split("|")
    action = parts[1]
    uid = cq.from_user.id
    msg = cq.message
    activity.touch(uid)
    try:
        if action == "help":
            edit(client, msg, lambda: (help_text(uid), _back_markup(uid)))
        elif action == "x" and len(parts) == 4:
            with _pending_lock:
                item = _AFTER.get(parts[2])
            original = item[0] if item and item[0].chat.id == uid else None
            if original is None:
                cq.answer(t(uid, "expired"), show_alert=True)
            else:
                activity.download(uid)
                cq.answer(t(uid, "started") if parts[3] == "a" else t(uid, "thumb"))
                fn = _send_thumbnail if parts[3] == "t" else (lambda c, m: _run_choice(c, m, "a"))
                threading.Thread(target=fn, args=(client, original), daemon=True).start()
        elif action == "home":
            edit(client, msg, lambda: (welcome_text(cq.from_user.first_name or "", uid), _start_markup(uid)))
        elif action == "close":
            msg.delete()
        elif action == "g" and len(parts) == 4:
            token, choice = parts[2], parts[3]
            with _pending_lock:
                item = _PENDING.get(token)
            original = item[0] if item and item[0].chat.id == uid else None
            if original is None:
                cq.answer(t(uid, "expired"), show_alert=True)
            elif choice == "t":
                activity.download(uid)
                cq.answer(t(uid, "thumb"))
                threading.Thread(target=_send_thumbnail, args=(client, original), daemon=True).start()
            elif _too_big(token, choice):
                size, fits = _too_big(token, choice)
                cq.answer()
                edit(client, msg, lambda: _too_big_view(uid, token, size, fits))
            else:
                with _pending_lock:
                    _PENDING.pop(token, None)  # one download per menu
                    picks = _SIZES.pop(token, None)
                activity.download(uid)
                cq.answer(t(uid, "started"))
                try:
                    msg.delete()
                except Exception:
                    pass
                threading.Thread(target=_run_choice, args=(client, original, choice, picks), daemon=True).start()
    except Exception as exc:
        if "MESSAGE_NOT_MODIFIED" not in str(exc):
            logger.warning(f"[ui] callback {cq.data} failed: {exc}")
    try:
        cq.answer()
    except Exception:
        pass
    cq.stop_propagation()
