"""Optional TeraBox public-share adapter.

This adapter uses a third-party extraction endpoint and is disabled by default.
It is intentionally independent of the unlicensed source ZIP. Endpoint availability,
terms and response formats can change; never treat this as a guaranteed service.
"""
from __future__ import annotations

import os
import re
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

TERABOX_HOSTS = {
    "terabox.com",
    "terabox.app",
    "terabox.fun",
    "terabox.best",
    "terabox.club",
    "terabox.click",
    "terabox.me",
    "terabox.site",
    "terabox.pro",
    "terabox.xyz",
    "teraboxapp.com",
    "teraboxlink.com",
    "teraboxlinke.com",
    "teraboxshare.com",
    "teraboxsharefile.com",
    "teraboxurl.com",
    "teraboxfree.com",
    "teraboxmod.app",
    "terasharelink.com",
    "terasharefile.com",
    "terashareus.com",
    "terafileshare.com",
    "terasharedrive.com",
    "tera1024box.com",
    "1024tera.com",
    "1024tera.co",
    "1024terabox.com",
    "1024-terabox.com",
    "1024box.com",
    "4funbox.com",
    "4funbox.co",
    "4funbox.in",
    "mirrobox.com",
    "nephobox.com",
    "freeterabox.com",
    "momerybox.com",
    "tibibox.com",
    "gibibox.com",
    "pebibox.com",
    "fancybox.in",
    "dubox.com",
    "bestclouddrive.com",
    "playduo.link",
}
API_ENDPOINT = os.getenv("TERABOX_API_ENDPOINT", "https://ytshorts.savetube.me/api/v1/terabox-downloader")
ENABLED = os.getenv("TERABOX_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
MAX_BYTES = int(float(os.getenv("TERABOX_MAX_BYTES", str(1900 * 1024 * 1024))))
CONNECT_TIMEOUT = float(os.getenv("TERABOX_CONNECT_TIMEOUT", "10"))
READ_TIMEOUT = float(os.getenv("TERABOX_READ_TIMEOUT", "30"))


def is_terabox_url(url: str) -> bool:
    try:
        parsed = urlparse(url.strip())
        host = (parsed.hostname or "").lower().rstrip(".")
        return parsed.scheme in {"http", "https"} and any(host == d or host.endswith("." + d) for d in TERABOX_HOSTS)
    except (ValueError, AttributeError):
        return False


def _download_thumb(url: str | None, folder: Path) -> str | None:
    if not url or urlparse(url).scheme not in {"http", "https"}:
        return None
    try:
        with requests.get(url, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT), stream=True) as r:
            r.raise_for_status()
            content_type = r.headers.get("Content-Type", "")
            if not content_type.startswith("image/"):
                return None
            path = folder / "thumb.jpg"
            with path.open("wb") as f:
                for chunk in r.iter_content(64 * 1024):
                    if chunk:
                        f.write(chunk)
                        if f.tell() > 5 * 1024 * 1024:
                            f.close()
                            path.unlink(missing_ok=True)
                            return None
            return str(path)
    except requests.RequestException:
        return None


def _extract_direct_link(data: dict) -> tuple[str | None, str | None, str | None]:
    items = data.get("response") or data.get("data") or []
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list) or not items:
        return None, None, None
    first = items[0] if isinstance(items[0], dict) else {}
    resolutions = first.get("resolutions") or {}
    if isinstance(resolutions, dict):
        candidate = resolutions.get("HD Video") or resolutions.get("Fast Download") or next(iter(resolutions.values()), None)
    else:
        candidate = None
    direct = first.get("download_url") or first.get("direct_link") or first.get("url") or candidate
    thumb = first.get("thumbnail") or first.get("thumb") or first.get("image")
    title = first.get("file_name") or first.get("name") or first.get("title") or "TeraBox video"
    if isinstance(direct, dict):
        direct = direct.get("url") or direct.get("link")
    return (direct if isinstance(direct, str) else None,
            thumb if isinstance(thumb, str) else None,
            title if isinstance(title, str) else "TeraBox video")


# ---------------- resolvers ----------------
# 1) native: share page (jsToken, logid) -> /share/list. Without a login it gives name/size only;
#    with the admin's TeraBox cookie (cookies/terabox.txt, "ndus") it also gives the dlink.
# 2) token API (like the ntmterabox.me API): TERABOX_API_URL + TERABOX_API_TOKEN in .env,
#    GET ?authkey=TOKEN&url=LINK -> {"ok": true, "files": [{"download_url", "filename", "size"}]}
# 3) the old public endpoint (API_ENDPOINT).
COOKIE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cookies", "terabox.txt")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0.0.0 Safari/537.36")
TOKEN_API_URL = os.getenv("TERABOX_API_URL", "").strip()
# Your own Cloudflare worker from github.com/mkhossainx/Terabox-Downloader-API (deployed with your cookie)
WORKER_URL = os.getenv("TERABOX_WORKER_URL", "").strip().rstrip("/")
TOKEN_API_TOKEN = os.getenv("TERABOX_API_TOKEN", "").strip()
PART_BYTES = int(1.95 * 1024 ** 3)       # Telegram bot upload limit per file
VIDEO_EXT = {".mp4", ".mkv", ".mov", ".webm", ".m4v", ".avi", ".flv", ".ts"}


def _max_bytes() -> int:
    try:
        from CONFIG.config import Config
        return int(float(getattr(Config, "MAX_FILE_SIZE_GB", 4)) * 1024 ** 3)
    except Exception:
        return MAX_BYTES


def _load_cookies(path: str = COOKIE_FILE) -> dict:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            rows = [l.rstrip("\n").split("\t") for l in f if l.strip() and not l.startswith("#")]
    except OSError:
        return {}
    return {r[5]: r[6] for r in rows if len(r) == 7}


def _cookie_files() -> list:
    """Cookie Manager TeraBox cookies (rotation order); the old single file as a fallback."""
    try:
        from COMMANDS import cookie_store
        paths = cookie_store.active_paths("terabox")
    except Exception:
        paths = []
    return paths or ([COOKIE_FILE] if os.path.exists(COOKIE_FILE) else [])


def _between(text: str, start: str, end: str) -> str | None:
    i = text.find(start)
    if i < 0:
        return None
    j = text.find(end, i + len(start))
    return text[i + len(start):j] if j > i else None


class NeedCookie(RuntimeError):
    """TeraBox showed the file but gives a download link only to a logged-in account."""

    def __init__(self, title, size):
        super().__init__("TeraBox needs a login cookie for this download")
        self.title, self.size = title, size


def _native_resolve(url: str, cookies: dict | None = None):
    """(direct_url, thumb, title, size, cookies) via TeraBox's own web API, or raise."""
    cookies = _load_cookies() if cookies is None else cookies
    sess = requests.Session()
    sess.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    sess.cookies.update(cookies)
    page = sess.get(url, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT), allow_redirects=True)
    page.raise_for_status()
    html = page.text
    js_token = _between(html, "fn%28%22", "%22%29") or _between(html, '"jsToken":"', '"')
    logid = _between(html, "dp-logid=", "&") or ""
    from urllib.parse import parse_qs
    final = urlparse(page.url)
    surl = (parse_qs(final.query).get("surl") or [""])[0]
    if not surl:
        m = re.search(r"/s/1?([\w-]+)", final.path) or re.search(r"/s/1?([\w-]+)", urlparse(url).path)
        surl = m.group(1) if m else ""
    if not js_token or not surl:
        raise RuntimeError("TeraBox page changed or the link is invalid")
    base = f"{final.scheme}://{final.hostname}"
    params = {"app_id": "250528", "web": "1", "channel": "dubox", "clienttype": "0", "jsToken": js_token,
              "dplogid": logid, "page": "1", "num": "20", "order": "time", "desc": "1",
              "site_referer": page.url, "shorturl": surl, "root": "1"}
    data = sess.get(f"{base}/share/list", params=params, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)).json()
    if data.get("errno") not in (0, None):
        raise RuntimeError(f"TeraBox refused the share (errno {data.get('errno')})")
    files = [f for f in data.get("list") or [] if not int(f.get("isdir") or 0)]
    if not files:
        raise RuntimeError("No file in this TeraBox share")
    f = max(files, key=lambda x: int(x.get("size") or 0))
    title, size = f.get("server_filename") or "TeraBox video", int(f.get("size") or 0)
    direct = f.get("dlink")
    if not direct or "ndus" not in cookies:
        raise NeedCookie(title, size)
    thumbs = f.get("thumbs") or {}
    return direct, thumbs.get("url3") or thumbs.get("url2"), title, size, sess.cookies.get_dict()


def _token_api_resolve(url: str):
    if not TOKEN_API_URL:
        raise RuntimeError("TERABOX_API_URL not set")
    params = {"url": url}
    if TOKEN_API_TOKEN:
        params["authkey"] = TOKEN_API_TOKEN
    with requests.get(TOKEN_API_URL, params=params, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)) as r:
        r.raise_for_status()
        data = r.json()
    files = [f for f in (data.get("files") or []) if f.get("download_url")] if data.get("ok") else []
    if not files:
        raise RuntimeError("token API returned no file")
    f = files[0]
    return f["download_url"], None, f.get("filename") or "TeraBox video", int(f.get("size") or 0), {}


def _worker_resolve(url: str):
    """POST {"link": url} -> {file_name, download_link, thumbnail, size_bytes, proxy_url}; download via proxy_url."""
    if not WORKER_URL:
        raise RuntimeError("TERABOX_WORKER_URL not set")
    with requests.post(WORKER_URL + "/", json={"link": url}, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)) as r:
        data = r.json()
    if data.get("error"):
        raise RuntimeError(f"worker: {data['error']}")
    direct = data.get("proxy_url") or data.get("download_link")
    if not direct or urlparse(direct).scheme not in {"http", "https"}:
        raise RuntimeError("worker returned no download link")
    return direct, data.get("thumbnail") or None, data.get("file_name") or "TeraBox video", int(data.get("size_bytes") or 0), {}


def _api_resolve(url: str):
    """Old public extraction endpoint."""
    with requests.post(API_ENDPOINT, json={"url": url}, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)) as response:
        response.raise_for_status()
        payload = response.json()
    direct, thumb, title = _extract_direct_link(payload)
    if not direct or urlparse(direct).scheme not in {"http", "https"}:
        raise RuntimeError("The extraction service returned no usable direct link")
    return direct, thumb, title, 0, {}


def _record(path, ok, reason=""):
    if not path:
        return
    try:
        from COMMANDS import cookie_store
        cookie_store.record(path, ok, reason)
    except Exception:
        pass


def _resolve(url: str):
    """First working resolver. On total failure raises; NeedCookie (with name/size) if TeraBox saw the file."""
    errors, need = [], None
    # 1) each TeraBox cookie in turn (Cookie Manager rotation), or no cookie for name/size
    for path in _cookie_files() or [None]:
        try:
            res = _native_resolve(url, _load_cookies(path) if path else {})
            _record(path, True)
            return res
        except NeedCookie as exc:
            need = exc
            _record(path, False, "TeraBox gave no download link")
        except Exception as exc:
            errors.append(f"native: {exc}")
    for fn in (_worker_resolve, _token_api_resolve, _api_resolve):
        try:
            return fn(url)
        except NeedCookie as exc:
            need = exc
        except Exception as exc:
            errors.append(f"{fn.__name__}: {exc}")
    print(f"[terabox] resolvers failed: {'; '.join(errors)}")
    if need:
        raise need
    raise RuntimeError("; ".join(errors))


# ---------------- send ----------------
def _duration(path: str) -> float:
    import subprocess
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                              "default=noprint_wrappers=1:nokey=1", path], capture_output=True, text=True, timeout=60)
        return float(out.stdout.strip() or 0)
    except Exception:
        return 0.0


def _caption(title: str, url: str, part: str = "") -> str:
    from html import escape
    from HELPERS.caption import build_quoted_caption
    bot = os.getenv("BOT_NAME", "Downloader_Villanbot")
    head = f"<b>{escape(title[:300])}</b>" + (f"\n{part}" if part else "")
    return build_quoted_caption(head, "", "", "", f'<a href="{escape(url)}">🔗 TeraBox</a> @{bot}')


def _send_file(app, message, filepath: Path, title: str, url: str, thumb_path):
    """Send the file; videos over the Telegram limit go out in parts."""
    chat, rid = message.chat.id, message.id
    size = filepath.stat().st_size
    is_video = filepath.suffix.lower() in VIDEO_EXT
    if size <= PART_BYTES:
        if is_video:
            app.send_video(chat, str(filepath), caption=_caption(title, url), thumb=thumb_path,
                           reply_to_message_id=rid, supports_streaming=True)
        else:
            app.send_document(chat, str(filepath), caption=_caption(title, url), thumb=thumb_path, reply_to_message_id=rid)
        return
    if not is_video:
        raise RuntimeError(f"too large: {size / 1024 ** 3:.2f} GB file (only videos can be sent in parts)")
    from DOWN_AND_UP.ffmpeg import split_video_2
    parts = split_video_2(str(filepath.parent), "part", str(filepath), size, PART_BYTES, _duration(str(filepath)), chat)
    paths = [p for p in (parts or {}).get("path", []) if os.path.exists(p) and os.path.getsize(p) > 0]
    if not paths:
        raise RuntimeError("could not split the video")
    for n, p in enumerate(paths, 1):
        app.send_video(chat, p, caption=_caption(title, url, f"Part {n}/{len(paths)}"), thumb=thumb_path,
                       reply_to_message_id=rid, supports_streaming=True)


def handle_terabox(app, message) -> bool:
    """Handle a TeraBox URL; return True if this adapter owns the message."""
    from HELPERS.progress_ui import progress_card
    from pyrogram import enums
    text = (getattr(message, "text", None) or "").strip()
    match = re.search(r"https?://[^\s<>]+", text)
    url = match.group(0).rstrip(".,);]}") if match else ""
    if not is_terabox_url(url):
        return False
    if not ENABLED:
        app.send_message(message.chat.id, "TeraBox support is disabled.", reply_to_message_id=message.id)
        return True

    html_mode = enums.ParseMode.HTML
    status = app.send_message(message.chat.id, progress_card("search", 0), parse_mode=html_mode, reply_to_message_id=message.id)
    limit = _max_bytes()
    try:
        direct_url, thumb_url, title, size, dl_cookies = _resolve(url)
        if size and size > limit:
            raise RuntimeError(f"too large: {size / 1024 ** 3:.2f} GB (limit {limit / 1024 ** 3:.0f} GB)")
        with tempfile.TemporaryDirectory(prefix="all-downloader-tera-") as temp_dir:
            folder = Path(temp_dir)
            name = title if "." in title else (Path(urlparse(direct_url).path).name or "terabox-video.mp4")
            filename = re.sub(r"[^\w .()\-]+", "_", name)[:120]
            if not filename or filename in {".", ".."}:
                filename = "terabox-video.mp4"
            filepath = folder / filename
            thumb_path = _download_thumb(thumb_url, folder)
            total, last_update, last_step = 0, 0.0, -1
            with requests.get(direct_url, stream=True, allow_redirects=True, cookies=dl_cookies or None,
                              headers={"User-Agent": UA, "Referer": "https://terabox.com/"},
                              timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)) as response:
                response.raise_for_status()
                length = int(response.headers.get("Content-Length") or size or 0)
                if length and length > limit:
                    raise RuntimeError(f"too large: {length / 1024 ** 3:.2f} GB (limit {limit / 1024 ** 3:.0f} GB)")
                with filepath.open("wb") as f:
                    for chunk in response.iter_content(1024 * 1024):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > limit:
                            raise RuntimeError(f"too large (limit {limit / 1024 ** 3:.0f} GB)")
                        f.write(chunk)
                        now = time.monotonic()
                        pct = total * 100 / length if length else 0
                        if now - last_update > 4 and int(pct // 10) != last_step:
                            last_update, last_step = now, int(pct // 10)
                            app.edit_message_text(message.chat.id, status.id,
                                                  progress_card("download", pct, detail=f"{total / 1024 ** 2:.0f} MB"),
                                                  parse_mode=html_mode)
            app.edit_message_text(message.chat.id, status.id, progress_card("upload", 0), parse_mode=html_mode)
            _send_file(app, message, filepath, title, url, thumb_path)
        app.delete_messages(message.chat.id, status.id)  # the file itself is the success signal
    except Exception as exc:
        print(f"[terabox] {url}: {exc}")
        if isinstance(exc, NeedCookie):
            body = (f"File: {exc.title[:80]} ({exc.size / 1024 ** 3:.2f} GB)\n"
                    "TeraBox gives download links only to a logged-in account. "
                    "Admin: add TeraBox cookies in Admin Panel → Cookie Manager.")
        elif "too large" in str(exc):
            body = str(exc)[:200]
        else:
            body = "The link may be private, deleted or need a password."
        try:
            from html import escape
            app.edit_message_text(message.chat.id, status.id,
                                  f"📦 <b>TeraBox download failed.</b>\n<blockquote><b>{escape(body)}</b></blockquote>",
                                  parse_mode=html_mode)
        except Exception:
            pass
    return True
