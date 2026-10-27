# PO Token Helper for YouTube
# Adds PO token provider support for YouTube domains

import requests
import time
import re
from CONFIG.config import Config
from CONFIG.messages import Messages, safe_get_messages
from URL_PARSERS.youtube import is_youtube_url
from HELPERS.logger import logger

# Cache for PO token provider availability checks
_pot_provider_cache = {
    'available': None,
    'last_check': 0,
    'check_interval': 30  # Check every 30 seconds
}

def check_pot_provider_availability(base_url: str) -> bool:
    """
    Check PO token provider availability.
    
    Args:
        base_url (str): Provider URL
        
    Returns:
        bool: True if the provider is available, otherwise False
    """
    current_time = time.time()
    
    # Check cache
    if (_pot_provider_cache['available'] is not None and 
        current_time - _pot_provider_cache['last_check'] < _pot_provider_cache['check_interval']):
        return _pot_provider_cache['available']
    
    try:
        # Fast availability check
        # The PO token provider may return 404 for the root path, which still means the service is running.
        response = requests.get(base_url, timeout=5)
        is_available = response.status_code in [200, 404]  # 404 means service is up but endpoint not found
        
        # Update cache
        _pot_provider_cache['available'] = is_available
        _pot_provider_cache['last_check'] = current_time
        
        if is_available:
            logger.info(f"PO token provider is available at {base_url} (status: {response.status_code})")
        else:
            logger.warning(f"PO token provider returned status {response.status_code} at {base_url}")
        
        return is_available
        
    except requests.exceptions.RequestException as e:
        logger.warning(f"PO token provider is not available at {base_url}: {e}")
        
        # Update cache
        _pot_provider_cache['available'] = False
        _pot_provider_cache['last_check'] = current_time
        
        return False

def add_pot_to_ytdl_opts(ytdl_opts: dict, url: str) -> dict:
    """
    Add PO token arguments to yt-dlp options for YouTube domains.
    
    Args:
        ytdl_opts (dict): yt-dlp options dict
        url (str): URL to check
        
    Returns:
        dict: Updated yt-dlp options dict
    """
    # Check whether the PO token provider is enabled
    if not getattr(Config, 'YOUTUBE_POT_ENABLED', False):
        messages = safe_get_messages()
        logger.info(messages.HELPER_POT_PROVIDER_DISABLED_MSG)
        return ytdl_opts
    
    # Check whether URL is a YouTube domain
    if not is_youtube_url(url):
        messages = safe_get_messages()
        logger.info(messages.HELPER_POT_URL_NOT_YOUTUBE_MSG.format(url=url))
        return ytdl_opts
    
    # Get provider base URL
    base_url = getattr(Config, 'YOUTUBE_POT_BASE_URL', 'http://127.0.0.1:4416')
    
    # Check PO token provider availability
    if not check_pot_provider_availability(base_url):
        messages = safe_get_messages()
        logger.warning(messages.HELPER_POT_PROVIDER_NOT_AVAILABLE_MSG.format(base_url=base_url))
        return ytdl_opts

    # yt-dlp reads provider settings from extractor_args["youtubepot-<provider>"]
    # (CLI: --extractor-args "youtubepot-bgutilhttp:base_url=..."). A nested
    # extractor_args["youtubepot"]["bgutilhttp"] is ignored, and the plugin then
    # falls back to 127.0.0.1:4416, which is empty inside the app container.
    ytdl_opts['extractor_args'] = dict(ytdl_opts.get('extractor_args') or {})
    ytdl_opts['extractor_args']['youtubepot-bgutilhttp'] = {'base_url': [base_url]}
    logger.info(f"PO token provider for YouTube: {base_url}")
    return ytdl_opts





def create_pot_debug_hook():
    messages = safe_get_messages(None)
    """
    Create a yt-dlp hook that intercepts and logs PO tokens.
    
    Returns:
        function: Hook function for yt-dlp
    """
    def pot_debug_hook(d):
        messages = safe_get_messages(None)
        """
        Hook to intercept PO tokens in yt-dlp.
        
        Args:
            d (dict): Download info dict
        """
        if d['status'] == 'downloading':
            # Look for PO tokens in URL or headers
            if 'url' in d:
                url = d['url']
                # Look for PO tokens in URL
                pot_patterns = [
                    r'po_token=([^&]+)',
                    r'popt=([^&]+)',
                    r'pot=([^&]+)',
                    r'proof_of_origin=([^&]+)'
                ]
                
                for pattern in pot_patterns:
                    match = re.search(pattern, url)
                    if match:
                        token = match.group(1)
                        logger.info(f"🎯 PO TOKEN DETECTED in URL: {token[:20]}...")
                        logger.info(f"🔗 Full URL with PO token: {url}")
                        break
                
                # Check headers for PO tokens
                if 'http_headers' in d:
                    headers = d['http_headers']
                    for header_name, header_value in headers.items():
                        if 'po' in header_name.lower() or 'token' in header_name.lower():
                            logger.info(f"🎯 PO TOKEN in header {header_name}: {header_value}")
        
        elif d['status'] == 'finished':
            # Log successful completion with PO tokens
            messages = safe_get_messages()
            logger.info(messages.HELPER_DOWNLOAD_FINISHED_PO_MSG)
            
    return pot_debug_hook


