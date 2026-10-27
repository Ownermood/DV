"""Small per-user download state shared by the download pipeline.

(Moved out of the old "Always Ask" quality menu, which the preview card replaced.)
"""
import hashlib
import json
import os

from CONFIG.logger_msg import LoggerMsg
from HELPERS.filesystem_hlp import create_directory
from HELPERS.logger import logger

_ASK_INFO_CACHE_FILE = "ask_formats.json"
_ASK_SUBS_LANGS_PREFIX = "ask_subs_"
_USER_DOWNLOAD_DIRS = {}




def set_user_download_dir(user_id, download_dir):
    """Set download directory for user session"""
    _USER_DOWNLOAD_DIRS[str(user_id)] = download_dir


def get_user_download_dir(user_id):
    """Get download directory for user session"""
    return _USER_DOWNLOAD_DIRS.get(str(user_id))


def generate_download_dir_name(url):
    """Generate download directory name based on URL with minimal sanitization - only replace unsupported characters"""
    try:
        from urllib.parse import urlparse
        import re
        
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        if domain.startswith('www.'):
            domain = domain[4:]
        
        # Start with domain
        dir_name = domain
        
        # Add path if exists and not just '/'
        if parsed.path and parsed.path != '/':
            path = parsed.path.strip('/')
            # Remove file extension if present
            path = re.sub(r'\.[a-zA-Z0-9]+$', '', path)
            if path:
                dir_name += f"_{path}"
        
        # Add query parameters if exist
        if parsed.query:
            # Add first few query parameters for all sites
            query_parts = parsed.query.split('&')[:3]
            for part in query_parts:
                if '=' in part:
                    key, value = part.split('=', 1)
                    # Only replace truly problematic characters, keep most symbols
                    key = re.sub(r'[^\w\-_.]', '_', key)
                    value = re.sub(r'[^\w\-_.]', '_', value)
                    dir_name += f"_{key}_{value}"
        
        # Only replace characters that are truly problematic for filesystem
        # Keep letters, numbers, hyphens, underscores, dots
        dir_name = re.sub(r'[^\w\-_.]', '_', dir_name)
        
        # Clean up multiple underscores
        dir_name = re.sub(r'_+', '_', dir_name)
        dir_name = dir_name.strip('_')
        
        # Limit length to reasonable size
        if len(dir_name) > 150:
            # Keep domain and first part, truncate rest
            parts = dir_name.split('_')
            if len(parts) > 1:
                domain_part = parts[0]
                remaining = '_'.join(parts[1:])
                if len(remaining) > 100:
                    remaining = remaining[:100]
                dir_name = f"{domain_part}_{remaining}"
            else:
                dir_name = dir_name[:150]
        
        # Ensure we have something
        if not dir_name or dir_name == '_':
            import hashlib
            url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
            dir_name = f"{domain}_{url_hash}"
        
        return dir_name
    except Exception as e:
        logger.warning(f"Failed to generate download directory name: {e}")
        try:
            from urllib.parse import urlparse
            import hashlib
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            if domain.startswith('www.'):
                domain = domain[4:]
            url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
            return f"{domain}_{url_hash}"
        except:
            return "unknown"


def _ask_cache_path(user_id):
    user_dir = os.path.join("users", str(user_id))
    create_directory(user_dir)
    return os.path.join(user_dir, _ASK_INFO_CACHE_FILE)


def _subs_langs_cache_path(user_id, url: str) -> str:
    user_dir = os.path.join("users", str(user_id))
    create_directory(user_dir)
    h = hashlib.sha1((url or "").encode("utf-8", errors="ignore")).hexdigest()[:16]
    return os.path.join(user_dir, f"{_ASK_SUBS_LANGS_PREFIX}{h}.json")


def save_subs_langs_cache(user_id: int, url: str, normal_langs, auto_langs) -> None:
    try:
        path = _subs_langs_cache_path(user_id, url)
        data = {
            "url": url,
            "normal": list(normal_langs or []),
            "auto": list(auto_langs or []),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


def load_subs_langs_cache(user_id: int, url: str):
    try:
        path = _subs_langs_cache_path(user_id, url)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("normal", []), data.get("auto", [])
    except Exception:
        return [], []
    return [], []


def delete_subs_langs_cache(user_id: int, url: str) -> None:
    try:
        path = _subs_langs_cache_path(user_id, url)
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def load_ask_info(user_id, url):
    try:
        # First try to find ask_formats.json in download directory
        download_dir = get_user_download_dir(user_id)
        if download_dir and os.path.exists(download_dir):
            download_cache_file = os.path.join(download_dir, _ASK_INFO_CACHE_FILE)
            if os.path.exists(download_cache_file):
                with open(download_cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                logger.info(f"Using ask_formats.json from download directory: {download_cache_file}")
                return data.get(url)
        
        # Fallback to user root directory
        path = _ask_cache_path(user_id)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            logger.info(f"Using ask_formats.json from user directory: {path}")
            return data.get(url)
    except Exception as e:
        logger.warning(f"Failed to load ask_formats.json: {e}")
        return None
    return None


def get_link_mode(user_id):
    """
    Get the user's LINK mode state.
    """
    try:
        user_dir = os.path.join("users", str(user_id))
        link_mode_file = os.path.join(user_dir, "link_mode.txt")
        if os.path.exists(link_mode_file):
            with open(link_mode_file, 'r') as f:
                return f.read().strip() == "enabled"
        return False
    except Exception:
        return False
