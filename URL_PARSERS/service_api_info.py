import re
import os
import shutil
from functools import lru_cache
from typing import Dict, Optional, Tuple
from datetime import datetime

import requests


DEFAULT_TIMEOUT_SECONDS = 6
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


def _load_cookies_for_user(user_id: int) -> Optional[str]:
    """
    Load cookies for a user.
    Returns the cookie file path or None.
    """
    if not user_id:
        return None
    
    user_dir = os.path.join("users", str(user_id))
    user_cookie_path = os.path.join(user_dir, "cookie.txt")
    
    # Check user cookies
    if os.path.exists(user_cookie_path):
        return user_cookie_path
    
    # Try global cookies
    try:
        from CONFIG._config import Config
        global_cookie_path = Config.COOKIE_FILE_PATH
        if os.path.exists(global_cookie_path):
            # Create user directory and copy global cookies
            os.makedirs(user_dir, exist_ok=True)
            shutil.copy2(global_cookie_path, user_cookie_path)
            return user_cookie_path
    except Exception as e:
        print(f"[COOKIES] Error loading global cookies: {e}")
    
    return None


def _create_session_with_cookies(user_id: int = None) -> requests.Session:
    """
    Create a requests session with user cookies.
    Uses managed session for automatic cleanup.
    """
    from HELPERS.http_manager import get_managed_session
    
    session_name = f"api-info-{user_id}" if user_id else "api-info-global"
    manager = get_managed_session(session_name)
    session = manager.get_session()
    
    # Set headers
    session.headers.update(DEFAULT_HEADERS)
    
    # Load cookies if user_id is provided
    if user_id:
        cookie_path = _load_cookies_for_user(user_id)
        if cookie_path:
            try:
                # Load cookies from file
                session.cookies = _load_cookies_from_file(cookie_path)
                print(f"[COOKIES] Loaded cookies for user {user_id} from {cookie_path}")
            except Exception as e:
                print(f"[COOKIES] Error loading cookies for user {user_id}: {e}")
    
    return session


def _load_cookies_from_file(cookie_path: str) -> requests.cookies.RequestsCookieJar:
    """
    Load cookies from a Netscape-format cookie file.
    """
    jar = requests.cookies.RequestsCookieJar()
    
    try:
        with open(cookie_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith('#') or not line:
                    continue
                
                parts = line.split('\t')
                if len(parts) >= 7:
                    domain = parts[0]
                    domain_flag = parts[1] == 'TRUE'
                    path = parts[2]
                    secure = parts[3] == 'TRUE'
                    expiration = parts[4]
                    name = parts[5]
                    value = parts[6]
                    
                    # Skip expired cookies (when expiration > 0)
                    if expiration != '0':
                        try:
                            exp_time = int(expiration)
                            if exp_time and exp_time > 0 and exp_time < int(datetime.now().timestamp()):
                                continue
                        except ValueError:
                            pass
                    
                    jar.set(name, value, domain=domain, path=path)
    except Exception as e:
        print(f"[COOKIES] Error parsing cookie file {cookie_path}: {e}")
    
    return jar


def _http_get(url: str, timeout: int = DEFAULT_TIMEOUT_SECONDS, user_id: int = None) -> Optional[str]:
    try:
        session = _create_session_with_cookies(user_id)
        resp = session.get(url, timeout=timeout, allow_redirects=True)
        if resp.ok:
            return resp.text
    except Exception as e:
        print(f"[HTTP_GET] Request error for {url}: {e}")
        return None
    return None


def _http_get_json(url: str, timeout: int = DEFAULT_TIMEOUT_SECONDS, user_id: int = None) -> Optional[Dict]:
    try:
        session = _create_session_with_cookies(user_id)
        resp = session.get(url, timeout=timeout, allow_redirects=True)
        if resp.ok:
            return resp.json()
        else:
            # Log HTTP errors for debugging, but do not interrupt the flow
            if resp.status_code == 401:
                print(f"[HTTP_GET_JSON] Authentication required for {url} (401 Unauthorized)")
            elif resp.status_code == 403:
                print(f"[HTTP_GET_JSON] Access forbidden for {url} (403 Forbidden)")
            else:
                print(f"[HTTP_GET_JSON] HTTP error {resp.status_code} for {url}")
    except Exception as e:
        print(f"[HTTP_GET_JSON] Request error for {url}: {e}")
        return None
    return None


META_TAG_RE = re.compile(
    r"<meta[^>]+(?:property|name)=\"([^\"]+)\"[^>]+content=\"([^\"]*)\"[^>]*>",
    re.IGNORECASE,
)


def _extract_meta(html: str) -> Dict[str, str]:
    metas: Dict[str, str] = {}
    if not html:
        return metas
    for match in META_TAG_RE.finditer(html):
        key = match.group(1).strip().lower()
        val = match.group(2).strip()
        metas[key] = val
    return metas


def _extract_via_oembed(url: str, endpoints: Tuple[str, ...], user_id: int = None) -> Tuple[Optional[str], Optional[str]]:
    """
    Try standard oEmbed endpoints and return (author_name/provider_name, site_label).
    """
    for ep in endpoints:
        try:
            full = ep.format(url=url)
            data = _http_get_json(full, user_id=user_id)
            if not data:
                continue
            author = data.get("author_name") or data.get("author_url") or data.get("title")
            provider = data.get("provider_name") or data.get("provider_url")
            # If author is a URL, normalize it
            if isinstance(author, str) and author.startswith("http"):
                # Take the last path segment
                try:
                    from urllib.parse import urlparse
                    p = urlparse(author)
                    segment = p.path.strip("/").split("/")
                    if segment and segment[0]:
                        author = segment[-1]
                except Exception:
                    pass
            return (author if isinstance(author, str) else None, provider if isinstance(provider, str) else None)
        except Exception as e:
            print(f"[OEMBED] Endpoint {ep} error: {e}")
            continue
    return (None, None)


def _normalize_slug(text: str) -> str:
    if not text:
        return ""
    # Remove emojis and non-word except spaces/underscore/dash
    text = re.sub(r"[\u2600-\u27BF\U0001F300-\U0001FAD6]+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.lower()
    # Keep Latin + Cyrillic letters (lowercased above) while stripping everything else.
    text = re.sub(r"[^a-z0-9\u0430-\u044f\u0451\s_-]", "", text)
    text = text.replace(" ", "_")
    return text


def _is_valid_username(token: str) -> bool:
    """Check that a token looks like a username: >=3 chars, contains letters, not digits-only."""
    if not token:
        return False
    token = token.strip()
    if len(token) < 3:
        return False
    if token.isdigit():
        return False
    if not re.search(r"[A-Za-z\u0410-\u042f\u0430-\u044f\u0401\u0451]", token):
        return False
    return True


def _parse_date_string(date_str: str) -> Optional[str]:
    """
    Parse a date from a string.
    Returns a date in DD.MM.YYYY format or None.
    """
    if not date_str:
        return None
    
    # Supported date formats
    date_formats = [
        "%Y-%m-%d",                    # 2024-10-04
        "%Y-%m-%dT%H:%M:%S",          # 2024-10-04T15:30:00
        "%Y-%m-%dT%H:%M:%SZ",         # 2024-10-04T15:30:00Z
        "%Y-%m-%dT%H:%M:%S.%f",       # 2024-10-04T15:30:00.123456
        "%Y-%m-%dT%H:%M:%S.%fZ",      # 2024-10-04T15:30:00.123456Z
        "%Y-%m-%d %H:%M:%S",          # 2024-10-04 15:30:00
        "%Y-%m-%d %H:%M:%S.%f",       # 2024-10-04 15:30:00.123456
        "%d.%m.%Y",                   # 04.10.2024
        "%d/%m/%Y",                   # 04/10/2024
        "%m/%d/%Y",                   # 10/04/2024
        "%B %d, %Y",                  # October 4, 2024
        "%d %B %Y",                   # 4 October 2024
    ]
    
    # Try standard formats first
    for fmt in date_formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.strftime("%d.%m.%Y")
        except ValueError:
            continue
    
    # If parsing failed, try extracting YYYY-MM-DD via regex
    date_match = re.search(r'(\d{4})-(\d{2})-(\d{2})', date_str)
    if date_match:
        year, month, day = date_match.groups()
        try:
            dt = datetime(int(year), int(month), int(day))
            return dt.strftime("%d.%m.%Y")
        except ValueError:
            pass
    
    # Try ISO format with timezone
    try:
        # Strip timezone suffix if present
        date_clean = re.sub(r'[+-]\d{2}:\d{2}$', '', date_str)
        dt = datetime.fromisoformat(date_clean)
        return dt.strftime("%d.%m.%Y")
    except ValueError:
        pass
    
    return None


def _guess_username_from_url(url: str, service: Optional[str]) -> Optional[str]:
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        path_parts = [p for p in (parsed.path or '').split('/') if p]
        if not path_parts:
            return None
        u = (url or '').lower()
        # Service-specific heuristics
        if service == 'tiktok':
            # https://www.tiktok.com/@username/... -> take after '@'
            for part in path_parts:
                if part.startswith('@') and _is_valid_username(part[1:]):
                    return part[1:]
        if service == 'instagram':
            blocked = {'p', 'reel', 'reels', 'stories', 'explore', 'tv', 's'}
            cand = path_parts[0]
            if cand not in blocked and _is_valid_username(cand):
                return cand
        if service in {'x', 'twitter'}:
            blocked = {'i', 'status', 'home', 'explore', 'notifications', 'share'}
            cand = path_parts[0]
            if cand not in blocked and _is_valid_username(cand):
                return cand
        if service == 'vk':
            # Prefer readable slugs like /somegroup; skip wall-, video, photo
            blocked_prefixes = ('wall', 'video', 'photo')
            cand = path_parts[0]
            if not any(cand.startswith(p) for p in blocked_prefixes) and _is_valid_username(cand):
                return cand
        if service in {'youtube'}:
            # /@channel, /channel/UC..., /c/name, /user/name
            if path_parts[0].startswith('@') and _is_valid_username(path_parts[0][1:]):
                return path_parts[0][1:]
            if len(path_parts) >= 2 and path_parts[0] in {'c', 'user'} and _is_valid_username(path_parts[1]):
                return path_parts[1]
        # Generic fallback: first segment that looks like username and not a known content marker
        generic_blocked = {'status', 'watch', 'post', 'p', 'reel', 'photo', 'video', 'wall'}
        for part in path_parts:
            if part in generic_blocked:
                continue
            # strip common file extensions
            part_clean = re.sub(r"\.(jpg|jpeg|png|gif|webp|mp4|mov|avi|mkv)$", "", part, flags=re.IGNORECASE)
            if _is_valid_username(part_clean):
                return part_clean
    except Exception:
        return None
    return None


def _detect_service(url: str) -> Optional[str]:
    """The bot's platforms only (see COMMANDS/simple_ui.PLATFORMS)."""
    if not url:
        return None
    u = url.lower()
    for key, marks in (("instagram", ("instagram.com",)), ("youtube", ("youtube.com", "youtu.be")),
                       ("pinterest", ("pinterest.", "pin.it")), ("facebook", ("facebook.com", "fb.watch")),
                       ("snapchat", ("snapchat.com",))):
        if any(m in u for m in marks):
            return key
    return None


# -------- Instagram --------


def _extract_instagram_info(url: str, user_id: int = None) -> Tuple[Optional[str], Optional[str]]:
    # Try oEmbed first with cookies
    try:
        data = _http_get_json(f"https://www.instagram.com/oembed/?url={url}", user_id=user_id)
        if data and isinstance(data, dict):
            author = data.get("author_name")
            provider = data.get("provider_name") or "Instagram"
            if author:
                return (author, provider)
    except Exception as e:
        print(f"[INSTAGRAM_INFO] oEmbed error: {e}")
    # Fallback to OpenGraph
    html = _http_get(url, user_id=user_id)
    metas = _extract_meta(html or "")
    title = metas.get("og:title") or metas.get("twitter:title")
    site = metas.get("og:site_name") or "Instagram"
    name: Optional[str] = None
    if title:
        # Patterns commonly seen
        patterns = [
            r"^(.*?) on Instagram",
            r"^(.*?) • Instagram",
            r"^Instagram post by (.*?)(?:\s+•|$)",
            r"^(.+?) \(@[^)]+\) • Instagram",
        ]
        for p in patterns:
            m = re.search(p, title, re.IGNORECASE)
            if m and m.group(1):
                name = m.group(1).strip()
                break
    return (name, site)


def _extract_instagram_date(url: str, user_id: int = None) -> Optional[str]:
    """
    Extract upload date from the Instagram oEmbed API.
    Returns a date in DD.MM.YYYY format or None.
    """
    try:
        data = _http_get_json(f"https://www.instagram.com/oembed/?url={url}", user_id=user_id)
        if data and isinstance(data, dict):
            # oEmbed may include the date in different fields
            date_str = data.get("upload_date") or data.get("created_at") or data.get("date")
            if date_str:
                return _parse_date_string(date_str)
    except Exception as e:
        # Log the error but do not interrupt the flow
        print(f"[INSTAGRAM_DATE] API error: {e}")
    
    # Fallback: try extracting from OpenGraph meta tags
    try:
        html = _http_get(url, user_id=user_id)
        metas = _extract_meta(html or "")
        
        # Look for the date in various meta tags
        date_fields = [
            "article:published_time",
            "article:modified_time", 
            "og:updated_time",
            "twitter:data1",
            "date",
            "pubdate"
        ]
        
        for field in date_fields:
            date_str = metas.get(field)
            if date_str:
                result = _parse_date_string(date_str)
                if result:
                    return result
    except Exception as e:
        print(f"[INSTAGRAM_DATE] OpenGraph error: {e}")
    
    return None


# -------- TikTok --------






# -------- X (Twitter) --------






# -------- VK --------




# -------- YouTube --------


def _extract_youtube_info(url: str, user_id: int = None) -> Tuple[Optional[str], Optional[str]]:
    # Try oEmbed with cookies first
    try:
        name, provider = _extract_via_oembed(url, (
            "https://www.youtube.com/oembed?url={url}",
        ), user_id=user_id)
        if name:
            return (name, provider or "YouTube")
    except Exception as e:
        print(f"[YOUTUBE_INFO] oEmbed with cookies error: {e}")
    
    # Fallback to oEmbed without cookies
    try:
        name, provider = _extract_via_oembed(url, (
            "https://www.youtube.com/oembed?url={url}",
        ))
        if name:
            return (name, provider or "YouTube")
    except Exception as e:
        print(f"[YOUTUBE_INFO] oEmbed without cookies error: {e}")
    
    # Final fallback to OpenGraph
    try:
        html = _http_get(url, user_id=user_id)
        metas = _extract_meta(html or "")
        channel = metas.get("og:site_name") or metas.get("twitter:title")
        return (channel, "YouTube")
    except Exception as e:
        print(f"[YOUTUBE_INFO] OpenGraph error: {e}")
        return (None, "YouTube")


def _extract_youtube_date(url: str, user_id: int = None) -> Optional[str]:
    """Extract upload date from YouTube API."""
    # Try with cookies first
    try:
        data = _http_get_json(f"https://www.youtube.com/oembed?url={url}", user_id=user_id)
        if data and isinstance(data, dict):
            date_str = data.get("upload_date") or data.get("created_at") or data.get("date")
            if date_str:
                return _parse_date_string(date_str)
    except Exception as e:
        print(f"[YOUTUBE_DATE] API with cookies error: {e}")
    
    # Fallback without cookies
    try:
        data = _http_get_json(f"https://www.youtube.com/oembed?url={url}")
        if data and isinstance(data, dict):
            date_str = data.get("upload_date") or data.get("created_at") or data.get("date")
            if date_str:
                return _parse_date_string(date_str)
    except Exception as e:
        print(f"[YOUTUBE_DATE] API without cookies error: {e}")
    
    # Final fallback to OpenGraph
    try:
        html = _http_get(url, user_id=user_id)
        metas = _extract_meta(html or "")
        date_str = metas.get("article:published_time") or metas.get("og:updated_time")
        if date_str:
            return _parse_date_string(date_str)
    except Exception as e:
        print(f"[YOUTUBE_DATE] OpenGraph error: {e}")
    
    return None


# -------- Reddit --------




# -------- Pinterest --------


def _extract_pinterest_info(url: str, user_id: int = None) -> Tuple[Optional[str], Optional[str]]:
    # No official public oEmbed; use OpenGraph only
    try:
        html = _http_get(url, user_id=user_id)
        metas = _extract_meta(html or "")
        title = metas.get("og:title") or metas.get("twitter:title")
        site = metas.get("og:site_name") or "Pinterest"
        return (title, site)
    except Exception as e:
        print(f"[PINTEREST_INFO] OpenGraph error: {e}")
        return (None, "Pinterest")


# -------- Flickr --------




# -------- DeviantArt --------




# -------- Imgur --------




# -------- Tumblr --------




# -------- Pixiv --------




# -------- ArtStation --------




# -------- Danbooru --------




# -------- Gelbooru --------




# -------- Yande.re --------




# -------- Sankaku --------




# -------- e621 --------




# -------- Rule34 --------




# -------- Behance --------




SERVICE_HANDLERS = {  # the bot's platforms (others only get the service tag)
    "instagram": _extract_instagram_info,
    "youtube": _extract_youtube_info,
    "pinterest": _extract_pinterest_info,
}

SERVICE_DATE_HANDLERS = {
    "instagram": _extract_instagram_date,
    "youtube": _extract_youtube_date,
}


@lru_cache(maxsize=512)
def get_service_account_info(url: str, user_id: int = None) -> Dict[str, Optional[str]]:
    """
    Return a dict with keys:
      - service: detected service name
      - account_display: readable name/handle (if detected)
      - site_label: human-readable site label (OG)
    Never raises exceptions.
    """
    try:
        service = _detect_service(url)
        if not service:
            return {"service": None, "account_display": None, "site_label": None}
        handler = SERVICE_HANDLERS.get(service)
        if not handler:
            return {"service": service, "account_display": None, "site_label": None}
        
        # Pass user_id to all handlers to support cookies
        if user_id:
            name, site_label = handler(url, user_id)
        else:
            name, site_label = handler(url)
            
        display = name if name else None
        if not display:
            # Fallback to URL-based guess if API/OG didn't provide
            guessed = _guess_username_from_url(url, service)
            if guessed:
                display = guessed
        return {"service": service, "account_display": display, "site_label": site_label}
    except Exception as e:
        print(f"[SERVICE_ACCOUNT_INFO] Error: {e}")
        return {"service": None, "account_display": None, "site_label": None}


def build_tags(info: Dict[str, Optional[str]]) -> Tuple[str, Optional[str]]:
    """
    Build (service_tag, account_tag) like ("#instagram", "#some_account").
    The second element can be None if the account was not detected.
    """
    service = info.get("service") or ""
    account = info.get("account_display") or ""
    if not service:
        return ("", None)
    service_tag = f"#{service}"
    account_tag: Optional[str] = None
    if account:
        slug = _normalize_slug(account)
        if slug:
            account_tag = f"#{slug}"
    return (service_tag, account_tag)


def get_account_tag(url: str, user_id: int = None) -> str:
    """
    Convenience helper: return a hashtag string for a message.
    Example: "#instagram #some_account" or just "#instagram".
    """
    info = get_service_account_info(url, user_id)
    service_tag, account_tag = build_tags(info)
    if service_tag and account_tag:
        return f"{service_tag} {account_tag}"
    return service_tag or ""


@lru_cache(maxsize=512)
def get_service_date(url: str, user_id: int = None) -> Optional[str]:
    """
    Extract upload/publish date from various service APIs.
    Returns a date in DD.MM.YYYY format or None.
    
    Supported services:
    - Instagram (oEmbed API) - supports cookies
    - TikTok (oEmbed API)
    - X/Twitter (oEmbed API)
    - YouTube (oEmbed API)
    - Others via OpenGraph meta tags
    
    IMPORTANT: This function is not critical for bot operation.
    If an API is unavailable (e.g., Instagram requires authentication),
    it returns None and the flow continues.
    """
    try:
        service = _detect_service(url)
        if not service:
            return None
            
        # Try a service-specific handler
        date_handler = SERVICE_DATE_HANDLERS.get(service)
        if date_handler:
            try:
                # Pass user_id to all date handlers to support cookies
                if user_id:
                    result = date_handler(url, user_id)
                else:
                    result = date_handler(url)
                    
                if result:
                    return result
            except Exception as e:
                # Log error but do not interrupt the flow
                print(f"[SERVICE_DATE] {service} API error: {e}")
        
        # Fallback: try extracting from OpenGraph meta tags
        try:
            html = _http_get(url, user_id=user_id)
            metas = _extract_meta(html or "")
            
            # Look for the date in various meta tags
            date_fields = [
                "article:published_time",
                "article:modified_time", 
                "og:updated_time",
                "twitter:data1",
                "date",
                "pubdate"
            ]
            
            for field in date_fields:
                date_str = metas.get(field)
                if date_str:
                    result = _parse_date_string(date_str)
                    if result:
                        return result
        except Exception as e:
            print(f"[SERVICE_DATE] OpenGraph error: {e}")
            
    except Exception as e:
        print(f"[SERVICE_DATE] General error: {e}")
    
    return None


__all__ = [
    "get_service_account_info",
    "build_tags",
    "get_account_tag",
    "get_service_date",
    "_parse_date_string",
]
