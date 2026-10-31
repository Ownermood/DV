"""Instagram photo / carousel posts without gallery-dl.

gallery-dl hangs on Instagram without a login (it sleeps on the rate limit), so a photo post sat on
"Downloading image…" forever. yt-dlp reads the same post at once: video items have formats, photo items
have none but carry the full-size image as their largest thumbnail. Everything is sent as albums of 10.
"""
from __future__ import annotations

import os
import tempfile

import requests
from pyrogram import enums
from pyrogram.types import InputMediaPhoto, InputMediaVideo

from HELPERS.logger import logger

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0.0.0 Safari/537.36")
ALBUM = 10   # Telegram media group limit


def _items(info) -> list:
    """[(kind, url)] for every item of the post: ("video", url) or ("photo", url)."""
    entries = info.get("entries") if info.get("_type") == "playlist" or info.get("entries") else [info]
    out = []
    for e in entries or []:
        if not e:
            continue
        fmts = [f for f in e.get("formats") or [] if f.get("url")]
        both = [f for f in fmts if f.get("vcodec") not in (None, "none") and f.get("acodec") not in (None, "none")]
        if both:   # a carousel video with sound in one file
            out.append(("video", max(both, key=lambda f: (f.get("height") or 0, f.get("tbr") or 0))["url"]))
            continue
        if fmts:   # video split in video + audio streams: the merged download needs yt-dlp
            out.append(("video_merge", e.get("webpage_url") or e.get("url") or ""))
            continue
        thumbs = [t for t in e.get("thumbnails") or [] if t.get("url")]
        if thumbs:  # photo: the largest (usually the last) thumbnail is the full-size image
            best = max(thumbs, key=lambda t: ((t.get("width") or 0) * (t.get("height") or 0), thumbs.index(t)))
            out.append(("photo", best["url"]))
    return out


def _probe(url):
    import yt_dlp
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "ignore_no_formats_error": True,
            "socket_timeout": 20, "extractor_retries": 1}
    try:
        from COMMANDS import cookie_store
        ck = cookie_store.first_active("instagram")
        if ck:
            opts["cookiefile"] = ck
    except Exception:
        pass
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def _fetch(url, path) -> bool:
    try:
        with requests.get(url, headers={"User-Agent": UA, "Referer": "https://www.instagram.com/"},
                          stream=True, timeout=(10, 60)) as r:
            r.raise_for_status()
            with open(path, "wb") as f:
                for chunk in r.iter_content(256 * 1024):
                    f.write(chunk)
        return os.path.getsize(path) > 0
    except Exception as exc:
        logger.info(f"[ig] fetch failed: {exc}")
        return False


def _merge_video(page_url, folder, n):
    """Video item with separate audio: yt-dlp downloads and merges it."""
    import yt_dlp
    out = os.path.join(folder, f"{n:02d}.%(ext)s")
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "outtmpl": out, "format": "bv*+ba/b",
                               "merge_output_format": "mp4", "socket_timeout": 20}) as ydl:
            ydl.download([page_url])
        return next((os.path.join(folder, f) for f in os.listdir(folder) if f.startswith(f"{n:02d}.")), None)
    except Exception as exc:
        logger.info(f"[ig] video {page_url}: {exc}")
        return None


def send_post(client, target, url, caption="") -> bool:
    """Send every photo / video of an Instagram post to target's chat. False when nothing could be read."""
    try:
        info = _probe(url)
    except Exception as exc:
        logger.info(f"[ig] {url}: {exc}")
        return False
    items = _items(info or {})
    if not items:
        return False
    with tempfile.TemporaryDirectory(prefix="ig-post-") as folder:
        media = []
        for n, (kind, src) in enumerate(items, 1):
            if kind == "video_merge":
                path = _merge_video(src, folder, n)
                kind = "video"
            else:
                path = os.path.join(folder, f"{n:02d}.{'mp4' if kind == 'video' else 'jpg'}")
                path = path if _fetch(src, path) else None
            if path:
                media.append((kind, path))
        if not media:
            return False
        sent = []
        for i in range(0, len(media), ALBUM):
            chunk = media[i:i + ALBUM]
            group = [(InputMediaVideo if k == "video" else InputMediaPhoto)(
                p, caption=caption if i == 0 and j == 0 else "", parse_mode=enums.ParseMode.HTML)
                for j, (k, p) in enumerate(chunk)]
            if len(group) == 1:
                k, p = chunk[0]
                send = client.send_video if k == "video" else client.send_photo
                sent.append(send(target.chat.id, p, caption=caption if i == 0 else "",
                                 parse_mode=enums.ParseMode.HTML, reply_to_message_id=target.id))
            else:
                sent.extend(client.send_media_group(target.chat.id, group, reply_to_message_id=target.id) or [])
        try:   # the photos also go to the image log channel
            from HELPERS.logger import get_log_channel
            log = get_log_channel("image")
            ids = [m.id for m in sent if getattr(m, "id", None)]
            if log and ids:
                client.forward_messages(log, target.chat.id, ids)
        except Exception as exc:
            logger.info(f"[ig] log forward failed: {exc}")
    logger.info(f"[ig] {url}: sent {len(media)} item(s)")
    return True
