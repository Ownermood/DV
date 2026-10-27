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
    "terabox.com", "www.terabox.com", "1024terabox.com", "www.1024terabox.com",
    "1024tera.com", "www.1024tera.com", "terabox.app", "www.terabox.app",
    "teraboxapp.com", "www.teraboxapp.com", "freeterabox.com", "www.freeterabox.com",
    "mirrobox.com", "www.mirrobox.com", "nephobox.com", "www.nephobox.com",
    "4funbox.com", "www.4funbox.com", "momerybox.com", "www.momerybox.com",
    "tibibox.com", "www.tibibox.com",
}
API_ENDPOINT = os.getenv("TERABOX_API_ENDPOINT", "https://ytshorts.savetube.me/api/v1/terabox-downloader")
ENABLED = os.getenv("TERABOX_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
MAX_BYTES = int(float(os.getenv("TERABOX_MAX_BYTES", str(1900 * 1024 * 1024))))
CONNECT_TIMEOUT = float(os.getenv("TERABOX_CONNECT_TIMEOUT", "10"))
READ_TIMEOUT = float(os.getenv("TERABOX_READ_TIMEOUT", "30"))


def is_terabox_url(url: str) -> bool:
    try:
        parsed = urlparse(url.strip())
        return parsed.scheme in {"http", "https"} and (parsed.hostname or "").lower().rstrip(".") in TERABOX_HOSTS
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


# ---------------- native resolver (TeraBox web API + an account cookie) ----------------
# Admin uploads cookies/terabox.txt (Netscape, must contain "ndus"). The share page gives
# jsToken + logid, /share/list then returns the file with a dlink that works with the cookie.
COOKIE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cookies", "terabox.txt")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0.0.0 Safari/537.36")


def _load_cookies() -> dict:
    try:
        with open(COOKIE_FILE, "r", encoding="utf-8", errors="ignore") as f:
            rows = [l.rstrip("\n").split("\t") for l in f if l.strip() and not l.startswith("#")]
    except OSError:
        return {}
    return {r[5]: r[6] for r in rows if len(r) == 7}


def _between(text: str, start: str, end: str) -> str | None:
    i = text.find(start)
    if i < 0:
        return None
    j = text.find(end, i + len(start))
    return text[i + len(start):j] if j > i else None


def _native_resolve(url: str):
    """(direct_url, thumb, title, size, cookies) via TeraBox's own web API, or raise."""
    cookies = _load_cookies()
    if "ndus" not in cookies:
        raise RuntimeError("no TeraBox cookie")
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
    f = max(files, key=lambda x: int(x.get("size") or 0)) if len(files) > 1 else files[0]
    direct = f.get("dlink")
    if not direct:
        raise RuntimeError("TeraBox gave no download link (cookie expired?)")
    thumbs = f.get("thumbs") or {}
    return direct, thumbs.get("url3") or thumbs.get("url2"), f.get("server_filename") or "TeraBox video", \
        int(f.get("size") or 0), sess.cookies.get_dict()


def _api_resolve(url: str):
    """Fallback: the configured third-party extraction endpoint."""
    with requests.post(API_ENDPOINT, json={"url": url}, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)) as response:
        response.raise_for_status()
        payload = response.json()
    direct, thumb, title = _extract_direct_link(payload)
    if not direct or urlparse(direct).scheme not in {"http", "https"}:
        raise RuntimeError("The extraction service returned no usable direct link")
    return direct, thumb, title, 0, {}


def _resolve(url: str):
    errors = []
    for fn in (_native_resolve, _api_resolve):
        try:
            return fn(url)
        except Exception as exc:
            errors.append(f"{fn.__name__}: {exc}")
    raise RuntimeError("; ".join(errors))


def handle_terabox(app, message) -> bool:
    """Handle a TeraBox URL; return True if this adapter owns the message."""
    text = (getattr(message, "text", None) or "").strip()
    match = re.search(r"https?://[^\s<>]+", text)
    url = match.group(0).rstrip(".,);]}") if match else ""
    if not is_terabox_url(url):
        return False
    if not ENABLED:
        app.send_message(message.chat.id, "TeraBox support is disabled. Set TERABOX_ENABLED=true after reviewing the optional adapter configuration.", reply_to_message_id=message.id)
        return True

    status = app.send_message(message.chat.id, "📦 <b>TeraBox link detected. Getting the file…</b>", reply_to_message_id=message.id)
    try:
        # Re-check the exact host before sending it anywhere.
        if not is_terabox_url(url):
            raise ValueError("Unsupported TeraBox hostname")
        direct_url, thumb_url, title, size, dl_cookies = _resolve(url)
        if size and size > MAX_BYTES:
            raise RuntimeError(f"too large ({size / 1024 ** 3:.2f} GB); TeraBox files are sent up to {MAX_BYTES / 1024 ** 3:.2f} GB")

        with tempfile.TemporaryDirectory(prefix="all-downloader-tera-") as temp_dir:
            folder = Path(temp_dir)
            filename = re.sub(r"[^\w .()\-]+", "_", title if "." in title else (Path(urlparse(direct_url).path).name or "terabox-video.mp4"))[:120]
            if not filename or filename in {".", ".."}:
                filename = "terabox-video.mp4"
            filepath = folder / filename
            thumb_path = _download_thumb(thumb_url, folder)
            total = 0
            last_update = 0.0
            with requests.get(direct_url, stream=True, allow_redirects=True, cookies=dl_cookies or None,
                              headers={"User-Agent": UA}, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)) as response:
                response.raise_for_status()
                length = response.headers.get("Content-Length")
                if length and int(length) > MAX_BYTES:
                    raise RuntimeError("The file exceeds TERABOX_MAX_BYTES")
                with filepath.open("wb") as f:
                    for chunk in response.iter_content(1024 * 512):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > MAX_BYTES:
                            raise RuntimeError("The file exceeded TERABOX_MAX_BYTES while downloading")
                        f.write(chunk)
                        now = time.monotonic()
                        if now - last_update > 4:
                            last_update = now
                            app.edit_message_text(message.chat.id, status.id, f"📦 <b>Downloading TeraBox file… {total / (1024 * 1024):.1f} MB</b>")
            from html import escape
            from HELPERS.caption import build_quoted_caption
            bot = os.getenv("BOT_NAME", "Downloader_Villanbot")
            caption = build_quoted_caption(f"<b>{escape(title[:300])}</b>", "", "", "",
                                           f'<a href="{escape(url)}">🔗 TeraBox</a> @{bot}')
            if filepath.suffix.lower() in {".mp4", ".mkv", ".mov", ".webm", ".m4v", ".avi"}:
                app.send_video(message.chat.id, str(filepath), caption=caption, thumb=thumb_path,
                               reply_to_message_id=message.id, supports_streaming=True)
            else:
                app.send_document(message.chat.id, str(filepath), caption=caption, thumb=thumb_path,
                                  reply_to_message_id=message.id)
        app.delete_messages(message.chat.id, status.id)  # the file itself is the success signal
    except Exception as exc:
        print(f"[terabox] {url}: {exc}")
        no_cookie = not os.path.exists(COOKIE_FILE)
        text = ("📦 <b>TeraBox download failed.</b>\n<blockquote><b>"
                + (str(exc)[:200] if "too large" in str(exc) else
                   "The link may be private, deleted or need a password."
                   + (" Admin: add a TeraBox cookie in Admin Panel → Settings." if no_cookie else ""))
                + "</b></blockquote>")
        try:
            app.edit_message_text(message.chat.id, status.id, text)
        except Exception:
            pass
    return True
