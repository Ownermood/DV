"""Small per-user download state shared by the download pipeline.

(Moved out of the old "Always Ask" quality menu, which the preview card replaced.)
"""
import hashlib
import os

from HELPERS.filesystem_hlp import create_directory
from HELPERS.logger import logger

_ASK_INFO_CACHE_FILE = "ask_formats.json"
_ASK_SUBS_LANGS_PREFIX = "ask_subs_"
_USER_DOWNLOAD_DIRS = {}






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










