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
from pyrogram.types import (InlineKeyboardMarkup, BotCommand)

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
GROUP_MENU = [
    ("dl", "Download a link: /dl <link>"),
    ("help", "How to use me in this group"),
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
    size = f"[filesize<?{cap}M][filesize_approx<?{cap}M]"
    if not height:
        return f"bv*{size}+ba/b{size}/b"
    # vertical videos (reels, shorts): "720p" is 720 wide x 1280 high, so they are matched on the width.
    # The last steps never fail: no such quality at all -> the best there is.
    v, h = f"[aspect_ratio<1][width<={int(height)}]", f"[height<={int(height)}]"
    return f"bv*{v}{size}+ba/bv*{h}{size}+ba/b{v}{size}/b{h}{size}/b{v}/b{h}/bv*{size}+ba/b"


def _is_admin(user_id: int) -> bool:
    try:
        return int(user_id) in Config.ADMIN
    except Exception:
        return False


def _add_group_url() -> str:
    me = (getattr(Config, "BOT_NAME", "") or "").lstrip("@")
    return f"https://t.me/{me}?startgroup=true" if me else ""


def _group_markup(user_id) -> Optional[InlineKeyboardMarkup]:
    url = _add_group_url()
    return InlineKeyboardMarkup([[btn("plus", t(user_id, "add_group"), url=url, style=BLUE)]]) if url else None


def group_text(title: str = "", user_id=None) -> str:
    name = html.escape(title.strip()) if title else "everyone"
    return t(user_id, "group_hi", group=name, me=html.escape((getattr(Config, "BOT_NAME", "") or "").lstrip("@")),
             max_gb=_max_gb(), platforms=_platform_lines())


def _start_markup(user_id) -> InlineKeyboardMarkup:
    rows = [[btn("question", t(user_id, "help"), cb="ui|help", style=GREEN)]]
    rows[0].append(btn("memo", "Settings", cb="st|home", style=BLUE))
    if _add_group_url():
        rows.append([btn("plus", t(user_id, "add_group"), url=_add_group_url(), style=BLUE)])
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
        client.set_bot_commands([BotCommand(c, d) for c, d in GROUP_MENU], scope=BotCommandScopeAllGroupChats())
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
            "socket_timeout": 15, "extractor_retries": 1, "retries": 1}  # the menu must answer fast
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
    order = [shared, None]  # admin's Cookie Manager cookie first (server IPs get YouTube's bot check without it)
    for cookie in dict.fromkeys(order):
        try:
            from HELPERS.proxy_helper import youtube_proxy
            from HELPERS.pot_helper import add_pot_to_ytdl_opts
            opts = add_pot_to_ytdl_opts(_ydl_opts(cookie), url)  # PO token provider (bgutil) for YouTube
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


def _thumb_bytes(url):
    """Thumbnail as an in-memory file (Instagram / Facebook CDN links can't be fetched by Telegram itself)."""
    if not url:
        return None
    try:
        import io
        import requests
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        if r.ok and r.content and len(r.content) < 10 * 1024 * 1024:
            f = io.BytesIO(r.content)
            f.name = "preview.jpg"
            return f
    except Exception as exc:
        logger.info(f"[ui] thumbnail fetch failed: {exc}")
    return None


def _yt_preview(info, picks, label="YouTube") -> str:
    """Caption: title, date / length / views / likes / comments / channel, then every quality with its size."""
    title = html.escape(info.get("title") or info.get("description") or label)[:180]
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
    if info.get("like_count"):
        meta.append(f"{em('heart')} {_count(info['like_count'])}")
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


def _owner_of(message) -> int:
    """Who sent the link: the user in a group, the chat itself in a private chat."""
    return getattr(getattr(message, "from_user", None), "id", None) or message.chat.id


def _incomplete_youtube(url) -> bool:
    """watch?v= / youtu.be / shorts link whose video ID is not the full 11 characters (copied badly)."""
    m = re.search(r"(?:[?&]v=|youtu\.be/|/shorts/|/live/|/embed/)([\w-]*)", url or "")
    return bool(m) and len(m.group(1)) != 11


def _show_link_menu(client, message, key, label) -> None:
    uid = message.chat.id
    if key == "youtube" and _incomplete_youtube(extract_url(message.text)):
        send(client, uid, lambda: (f"{em('warn')} <b>This YouTube link is incomplete.</b>\n"
                                   "<blockquote>Part of the video ID is missing. Copy the full link and send it again.</blockquote>",
                                   None), reply_to_message_id=message.id)
        return
    from HELPERS import cancel
    cancel.clear(uid)  # a new link: an old Cancel tap must not stop it
    from COMMANDS.user_prefs import quality as _default_quality
    pref = _default_quality(_owner_of(message), key)
    token = _remember(message, key)
    menu = send(client, uid, lambda: (t(uid, "loading"), None), reply_to_message_id=message.id)

    def work():
        info = _probe(extract_url(message.text), uid)
        picks = _yt_pick(info) if info else None
        sizes = {h: p[1] for h, p in picks.items()} if picks else None
        if picks:
            _SIZES[token] = picks
        title = html.escape((info or {}).get("title") or label)[:200]
        auto = _pref_choice(pref, picks) if pref and (info or key == "youtube") else None
        if auto:  # ⚙️ the person's default quality: no menu, start right away
            try:
                menu.delete()
            except Exception:
                pass
            with _pending_lock:
                _PENDING.pop(token, None)
                _SIZES.pop(token, None)
            activity.download(uid)
            _run_choice(client, message, auto, picks)
            return
        if not info and key != "youtube":  # photo posts / no info (gallery-dl sites): download straight away
            try:
                menu.delete()
            except Exception:
                pass
            with _pending_lock:
                _PENDING.pop(token, None)
            activity.download(uid)
            if key in ("instagram", "pinterest", "facebook", "snapchat"):  # photo post: gallery-dl straight away
                try:
                    from COMMANDS.image_cmd import image_command
                    from HELPERS.safe_messeger import fake_message
                    image_command(client, fake_message(f"/img {extract_url(message.text)}", _owner_of(message),
                                                       original_chat_id=uid, original_message=message))
                    return
                except Exception as exc:
                    logger.warning(f"[ui] image download failed, trying the video pipeline: {exc}")
            _run_choice(client, message, "b")
            return
        if not info:  # yt-dlp blocked (server IP): title, channel and thumbnail from oEmbed still make a preview
            info = _oembed(extract_url(message.text))
        if info and info.get("id"):
            try:  # preview card: thumbnail + details + sizes (like the big downloader bots)
                kb = _choice_rows(uid, token, sizes, is_yt=bool(sizes) or key == "youtube")
                photo = (f"https://i.ytimg.com/vi/{info['id']}/hqdefault.jpg" if key == "youtube"
                         else _thumb_bytes(info.get("thumbnail")))
                if photo:
                    send(client, uid, lambda: (_yt_preview(info, picks, label), kb), photo=photo,
                         reply_to_message_id=message.id)
                    menu.delete()
                    return
            except Exception as exc:
                logger.warning(f"[ui] preview failed, text menu instead: {exc}")
        if info and key != "youtube":  # no thumbnail: same details as text
            try:
                edit(client, menu, lambda: (_yt_preview(info, picks, label),
                                            _choice_rows(uid, token, sizes, is_yt=bool(sizes))))
                return
            except Exception as exc:
                logger.warning(f"[ui] menu edit failed: {exc}")
        try:
            edit(client, menu, lambda: (t(uid, "pick_yt", title=title, max_gb=_max_gb()),
                                        _choice_rows(uid, token, sizes, is_yt=True)))
        except Exception as exc:
            logger.warning(f"[ui] yt menu edit failed: {exc}")
    threading.Thread(target=work, daemon=True).start()


def _pref_choice(pref, picks):
    """Menu choice for a default quality: MP3 / best as is; a height falls to the next lower one
    that fits the size limit. None = no such quality fits: show the menu."""
    if pref == "a" or not picks:
        return pref
    fits = [h for h, p in picks.items() if p[0]]
    if pref == "b":
        return "b" if fits else None
    lower = [h for h in fits if h <= int(pref)]
    pick = max(lower) if lower else (min(fits) if fits else None)
    return str(pick) if pick else None


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
    from HELPERS import cancel
    cancel.clear(message.chat.id)  # a new download: an old Cancel tap must not stop it
    cancel.set_owner(message.chat.id, _owner_of(message))
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
            from COMMANDS.user_prefs import quality as _default_quality
            found = detect_platform(url)
            pref = _default_quality(_owner_of(m), found[0]) if found and found[0] != "terabox" else None
            _run_choice(client, m, pref or "b")   # waits until this link is sent
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


GROUP_COMMANDS = ("dl", "download")


def _group_link(client, message):
    """Link to download from a group message: "/dl <link>", "/dl" as a reply to a link, or a mention + link.
    None when the message is not for the bot."""
    text = message.text or ""
    first = text.split()[0].lower() if text.split() else ""
    me = (getattr(Config, "BOT_NAME", "") or "").lower().lstrip("@")
    is_cmd = first.startswith("/") and first[1:].split("@")[0] in GROUP_COMMANDS and (
        "@" not in first or first.split("@", 1)[1] == me)
    mentioned = bool(me) and f"@{me}" in text.lower()
    replied = getattr(message, "reply_to_message", None)
    if not (is_cmd or mentioned):
        return None
    url = extract_url(text)
    if not url and replied:  # "/dl" (or a mention) as a reply to a message with the link
        url = extract_url(replied.text or replied.caption or "")
    return url or ""


_ADMIN_TTL = 600
_bot_admin = {}   # group chat -> (is admin, checked at)


def _is_bot_admin(client, chat_id) -> bool:
    """The bot works in a group only as an admin. Cached 10 min; promotions update it at once."""
    ok, at = _bot_admin.get(chat_id, (None, 0.0))
    if ok is not None and time.time() - at < _ADMIN_TTL:
        return ok
    try:
        status = client.get_chat_member(chat_id, "me").status
        ok = status in (enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER)
    except Exception as exc:
        logger.info(f"[group] admin check {chat_id}: {exc}")
        ok = False
    _bot_admin[chat_id] = (ok, time.time())
    return ok


@app.on_chat_member_updated(filters.group)
def bot_promoted(client, update):
    """The bot's own rights changed in a group: remember it, and say thanks when promoted."""
    new = getattr(update, "new_chat_member", None)
    if not new or not getattr(getattr(new, "user", None), "is_self", False):
        return
    uid = update.chat.id
    ok = new.status in (enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER)
    was = _bot_admin.get(uid, (None, 0.0))[0]
    _bot_admin[uid] = (ok, time.time())
    old = getattr(getattr(update, "old_chat_member", None), "status", None)
    if ok and not was and old not in (enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER):
        send(client, uid, lambda: (t(uid, "admin_ready"), None))


@app.on_message(filters.group & filters.text, group=-1)
def group_door(client, message):
    """Groups: anyone can use the bot with "/dl <link>" or "@bot <link>"; everything else in the group is ignored."""
    if not getattr(message, "from_user", None) or message.from_user.is_bot:
        return
    _ensure_menu(client)
    uid = message.chat.id
    words = (message.text or "").split()
    cmd = words[0].lower() if words else ""
    me = (getattr(Config, "BOT_NAME", "") or "").lower().lstrip("@")
    if cmd.startswith("/") and cmd[1:].split("@")[0] in ("start", "help") and (
            "@" not in cmd or cmd.split("@", 1)[1] == me):
        send(client, uid, lambda: (group_text(message.chat.title or "", uid), _group_markup(uid)),
             reply_to_message_id=message.id, **_topic(message))
        message.stop_propagation()
    url = _group_link(client, message)
    if url is None:
        return
    if not _is_bot_admin(client, uid):
        send(client, uid, lambda: (t(uid, "need_admin"), None), reply_to_message_id=message.id, **_topic(message))
        message.stop_propagation()
    activity.touch(message.from_user.id)
    if not url:
        hint = send(client, uid, lambda: (f"{em('down')} <b>Send a link with the command:</b>\n"
                                   f"<blockquote><code>/dl https://…</code>\nor <code>@{getattr(Config, 'BOT_NAME', '')} https://…</code>\n"
                                   "or reply <code>/dl</code> to a message that has the link.</blockquote>", None),
             reply_to_message_id=message.id, **_topic(message))
        _delete_later(client, uid, [message.id, getattr(hint, "id", None)])
        message.stop_propagation()
    found = detect_platform(url)
    if found is None:
        hint = send(client, uid, lambda: (unsupported_text(uid), None), reply_to_message_id=message.id, **_topic(message))
        _delete_later(client, uid, [message.id, getattr(hint, "id", None)])
        message.stop_propagation()
    person = message.from_user
    group = message.chat.title or "the group"
    name = _mention(person)
    if _start_in_dm(client, person, url, group):
        note = send(client, uid, lambda: (t(uid, "dm_sent", name=name), _dm_markup()), reply_to_message_id=message.id, **_topic(message))
    else:  # never started the bot: Telegram lets a bot DM only people who started it
        token = secrets.token_hex(6)
        with _pending_lock:
            now = time.time()
            for k in [k for k, v in _DM_WAIT.items() if now - v[3] > _DM_WAIT_TTL]:
                _DM_WAIT.pop(k, None)
            _DM_WAIT[token] = (person.id, url, group, now)
        note = send(client, uid, lambda: (t(uid, "dm_start", name=name), _dm_markup(f"dl{token}")),
                    reply_to_message_id=message.id, **_topic(message))
    _delete_later(client, uid, [message.id, getattr(note, "id", None)])
    message.stop_propagation()


def _topic(message) -> dict:
    """Forum groups: answer inside the same topic the command came from, never in another one."""
    tid = getattr(message, "message_thread_id", None)
    return {"message_thread_id": tid} if tid and getattr(message, "is_topic_message", False) else {}


GROUP_CLEAN_AFTER = 60          # the /dl message and the bot's note leave the group after 1 min
_DM_WAIT = {}                   # start token -> (user id, url, group title, since)
_DM_WAIT_TTL = 30 * 60


def _mention(user) -> str:
    if getattr(user, "username", None):
        return "@" + user.username
    return f'<a href="tg://user?id={user.id}">{html.escape((user.first_name or "there")[:40])}</a>'


def _dm_markup(start=None) -> Optional[InlineKeyboardMarkup]:
    me = (getattr(Config, "BOT_NAME", "") or "").lstrip("@")
    if not me:
        return None
    if start:
        return InlineKeyboardMarkup([[btn("play", "Start Bot", url=f"https://t.me/{me}?start={start}", style=GREEN)]])
    return InlineKeyboardMarkup([[btn("chat", "Open DM", url=f"https://t.me/{me}", style=BLUE)]])


def _delete_later(client, chat_id, ids) -> None:
    ids = [i for i in ids if i]

    def run():
        try:
            client.delete_messages(chat_id, ids)
        except Exception as exc:
            logger.info(f"[group] clean-up in {chat_id} failed: {exc}")
    timer = threading.Timer(GROUP_CLEAN_AFTER, run)
    timer.daemon = True
    timer.start()


def _start_in_dm(client, person, url, group) -> bool:
    """Run a group request in the person's DM: the link card there, then the usual preview / download.
    False when the bot can't write to them (they never started it, or blocked it)."""
    try:
        dm = client.send_message(person.id, t(person.id, "dm_link", group=html.escape(group[:60]), url=html.escape(url)),
                                 parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
    except Exception as exc:
        logger.info(f"[group] can't DM {person.id}: {exc}")
        return False
    dm.from_user = person      # the bot wrote it; the pipeline must see the person
    dm.text = url              # the pipeline reads the link from the message text
    _run_link(client, dm, url)
    return True


def _run_link(client, message, url) -> None:
    """Preview card (or TeraBox menu) for one link, like a link sent in private."""
    found = detect_platform(url)
    if not found:
        return
    key, label, _, _ = found
    if key == "terabox":
        from URL_PARSERS.url_extractor import url_distractor
        threading.Thread(target=url_distractor, args=(client, message), daemon=True).start()
    else:
        threading.Thread(target=_show_link_menu, args=(client, message, key, label), daemon=True).start()


def _resume_from_group(client, user, token) -> bool:
    """/start dl<token> from the group's Start Bot button: run the waiting link in this DM."""
    with _pending_lock:
        item = _DM_WAIT.get(token)
        if not item or item[0] != user.id:
            return False
        _DM_WAIT.pop(token, None)
    return _start_in_dm(client, user, item[1], item[2])


def join_card(client, message) -> None:
    """Force-join: Join Channel + I've Joined (re-checks and starts the same link)."""
    uid = message.chat.id
    url = getattr(Config, "SUBSCRIBE_CHANNEL_URL", "") or ""
    m = re.search(r"t\.me/([A-Za-z0-9_]{4,})/?$", url)
    channel = f"<b>@{m.group(1)}</b>" if m else "<b>our channel</b>"
    token = secrets.token_hex(4)
    with _pending_lock:
        now = time.time()
        for k in [k for k, v in _JOIN.items() if now - v[2] > _DM_WAIT_TTL]:
            _JOIN.pop(k, None)
        _JOIN[token] = (message, extract_url(getattr(message, "text", "") or ""), now)
    rows = [[btn("plus", "Join Channel", url=url, style=BLUE)]] if url else []
    rows.append([btn("ok", "I've Joined", cb=f"ui|fj|{token}", style=GREEN)])
    send(client, uid, lambda: (t(uid, "join_needed", channel=channel), InlineKeyboardMarkup(rows)))


_JOIN = {}                      # token -> (message, url, since)


@app.on_message(filters.group & filters.new_chat_members, group=-1)
def group_welcome(client, message):
    """The bot was added to a group: greet and show how to use it."""
    if not any(getattr(m, "is_self", False) for m in message.new_chat_members or []):
        return
    _ensure_menu(client)
    uid = message.chat.id
    admin = _is_bot_admin(client, uid)
    send(client, uid, lambda: (group_text(message.chat.title or "", uid)
                               + ("" if admin else "\n\n" + t(uid, "need_admin")), _group_markup(uid)), **_topic(message))
    message.stop_propagation()


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

    if text.startswith("/") and text[1:].split()[0].split("@")[0].lower() in GROUP_COMMANDS and _URL_RE.search(text):
        text = message.text = text.split(None, 1)[1].strip()   # "/dl <link>" works in private too
    if text.startswith("/"):
        cmd = text[1:].split()[0].split("@")[0].lower()
        if cmd == "start":
            payload = text.split(None, 1)[1].strip() if len(text.split()) > 1 else ""
            if payload.startswith("dl") and message.from_user and _resume_from_group(client, message.from_user, payload[2:]):
                message.stop_propagation()   # the group link runs here now
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
    _show_link_menu(client, message, key, label)   # preview card + qualities; no info (photo posts) -> direct download
    message.stop_propagation()


@app.on_callback_query(filters.regex(r"^cx\|"), group=-1)
def cancel_download(client, cq):
    """❌ Cancel on a downloading / uploading card (only the user who started it)."""
    from HELPERS import cancel
    try:
        uid = int(cq.data.split("|")[1])  # the chat (a user, or a group)
        if cq.from_user.id not in (uid, cancel.owner(uid)):
            cq.answer("Only the person who sent the link can cancel it.", show_alert=True)
        else:
            cancel.request(uid)
            with _queue_lock:  # a running link queue stops too
                _queues.pop(uid, None)
            cq.answer("Cancelled")
            edit(client, cq.message, lambda: (f"{em('ban')} <b>Download cancelled.</b>", None))
    except Exception as exc:
        logger.warning(f"[ui] cancel failed: {exc}")
    cq.stop_propagation()


@app.on_callback_query(filters.regex(r"^tb\|"), group=-1)
def terabox_choice(client, cq):
    from ADAPTERS.terabox import on_choice
    try:
        on_choice(client, cq)
    except Exception as exc:
        logger.warning(f"[ui] terabox choice failed: {exc}")
    cq.stop_propagation()


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
            original = item[0] if item and _owner_of(item[0]) == uid else None
            if original is None:
                cq.answer(t(uid, "expired"), show_alert=True)
            else:
                activity.download(uid)
                cq.answer(t(uid, "started") if parts[3] == "a" else t(uid, "thumb"))
                fn = _send_thumbnail if parts[3] == "t" else (lambda c, m: _run_choice(c, m, "a"))
                threading.Thread(target=fn, args=(client, original), daemon=True).start()
        elif action == "fj" and len(parts) == 3:
            from HELPERS.limitter import is_member
            with _pending_lock:
                item = _JOIN.get(parts[2])
            if not item or _owner_of(item[0]) != uid:
                cq.answer(t(uid, "expired"), show_alert=True)
            elif not is_member(client, uid):
                cq.answer("You haven't joined yet. Join the channel, then tap again.", show_alert=True)
            else:
                with _pending_lock:
                    _JOIN.pop(parts[2], None)
                cq.answer("Thanks for joining!")
                msg.delete()
                if item[1]:
                    item[0].text = item[1]
                    _run_link(client, item[0], item[1])
        elif action == "home":
            edit(client, msg, lambda: (welcome_text(cq.from_user.first_name or "", uid), _start_markup(uid)))
        elif action == "close":
            msg.delete()
        elif action == "g" and len(parts) == 4:
            token, choice = parts[2], parts[3]
            with _pending_lock:
                item = _PENDING.get(token)
            original = item[0] if item and _owner_of(item[0]) == uid else None
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
