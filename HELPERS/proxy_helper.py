import yt_dlp
import logging
import os
from urllib.parse import quote
from COMMANDS.proxy_cmd import get_proxy_config, get_proxy_2_config  # None when no real proxy is set
from CONFIG.messages import Messages, safe_get_messages

logger = logging.getLogger(__name__)


def build_proxy_url(proxy_config):
    """Build proxy URL from configuration"""
    if not proxy_config or 'type' not in proxy_config or 'ip' not in proxy_config or 'port' not in proxy_config:
        return None
    
    if proxy_config['type'] == 'http':
        if proxy_config.get('user') and proxy_config.get('password'):
            return f"http://{proxy_config['user']}:{proxy_config['password']}@{proxy_config['ip']}:{proxy_config['port']}"
        else:
            return f"http://{proxy_config['ip']}:{proxy_config['port']}"
    elif proxy_config['type'] == 'https':
        if proxy_config.get('user') and proxy_config.get('password'):
            return f"https://{proxy_config['user']}:{proxy_config['password']}@{proxy_config['ip']}:{proxy_config['port']}"
        else:
            return f"https://{proxy_config['ip']}:{proxy_config['port']}"
    elif proxy_config['type'] in ['socks4', 'socks5', 'socks5h']:
        if proxy_config.get('user') and proxy_config.get('password'):
            return f"{proxy_config['type']}://{proxy_config['user']}:{proxy_config['password']}@{proxy_config['ip']}:{proxy_config['port']}"
        else:
            return f"{proxy_config['type']}://{proxy_config['ip']}:{proxy_config['port']}"
    else:
        if proxy_config.get('user') and proxy_config.get('password'):
            return f"http://{proxy_config['user']}:{proxy_config['password']}@{proxy_config['ip']}:{proxy_config['port']}"
        else:
            return f"http://{proxy_config['ip']}:{proxy_config['port']}"

def youtube_proxy(url: str):
    """YOUTUBE_PROXY from .env (socks5h://warp:1080 with the warp container) for YouTube URLs, else None."""
    proxy = os.getenv("YOUTUBE_PROXY", "").strip()
    u = (url or "").lower()
    return proxy if proxy and ("youtube.com" in u or "youtu.be" in u) else None


def add_proxy_to_ytdl_opts(ytdl_opts: dict, url: str, user_id: int = None) -> dict:
    """Add proxy to yt-dlp options if proxy is enabled for user or domain requires it"""
    logger.info(f"add_proxy_to_ytdl_opts called: user_id={user_id}, url={url}")
    
    # GLOBAL SAFETY: initialize messages
    messages = safe_get_messages(user_id)

    # Priority 0: YOUTUBE_PROXY (e.g. the warp container) for YouTube links only
    yt_proxy = youtube_proxy(url)
    if yt_proxy:
        ytdl_opts['proxy'] = yt_proxy
        return ytdl_opts
    
    # Priority 1: Check if user has proxy enabled (/proxy on)
    if user_id:
        try:
            from COMMANDS.proxy_cmd import is_proxy_enabled
            proxy_enabled = is_proxy_enabled(user_id)
            logger.info(f"User {user_id} proxy enabled: {proxy_enabled}")
            if proxy_enabled:
                # Use round-robin/random selection for user proxy
                proxy_config = select_proxy_for_user()
                if proxy_config:
                    proxy_url = build_proxy_url(proxy_config)
                    if proxy_url:
                        ytdl_opts['proxy'] = proxy_url
                        logger.info(f"Added user proxy for {user_id}: {proxy_url}")
                        return ytdl_opts
        except Exception as e:
            logger.warning(f"Error checking proxy for user {user_id}: {e}")
            pass
    
    # Priority 2: Check if domain requires specific proxy (only if user proxy is OFF)
    logger.info(f"Checking domain-specific proxy for {url}")
    proxy_config = select_proxy_for_domain(url)
    if proxy_config:
        proxy_url = build_proxy_url(proxy_config)
        if proxy_url:
            ytdl_opts['proxy'] = proxy_url
            logger.info(f"Added domain-specific proxy for {url}: {proxy_url}")
        else:
            logger.warning(f"Failed to build proxy URL from config: {proxy_config}")
    else:
        logger.info(f"No domain-specific proxy found for {url}")
    
    return ytdl_opts

def try_with_proxy_fallback(ytdl_opts: dict, url: str, user_id: int = None, operation_func=None, *args, **kwargs):
    """
    Try operation with different proxies in case of failure when user proxy is enabled
    
    Args:
        ytdl_opts: yt-dlp options
        url: URL to process
        user_id: User ID
        operation_func: Function to call with ytdl_opts
        *args, **kwargs: Additional arguments for operation_func
    
    Returns:
        Result of operation_func or None if all proxies failed
    """
    messages = safe_get_messages(user_id)
    if not operation_func:
        return None
    
    # Check if user has proxy enabled
    if not user_id:
        # No user ID, try without proxy fallback
        return operation_func(ytdl_opts, *args, **kwargs)
    
    try:
        from COMMANDS.proxy_cmd import is_proxy_enabled
        proxy_enabled = is_proxy_enabled(user_id)
        if not proxy_enabled:
            # User proxy is disabled, try without proxy fallback
            return operation_func(ytdl_opts, *args, **kwargs)
    except Exception as e:
        logger.warning(f"Error checking proxy for user {user_id}: {e}")
        return operation_func(ytdl_opts, *args, **kwargs)
    
    # User proxy is enabled, get all available proxies
    all_configs = get_all_proxy_configs()
    if not all_configs:
        logger.info(f"No proxies available for {url}, trying without proxy")
        return operation_func(ytdl_opts, *args, **kwargs)
    
    # Try with each proxy
    for i, proxy_config in enumerate(all_configs):
        try:
            # Update proxy in options
            current_opts = ytdl_opts.copy()
            proxy_url = build_proxy_url(proxy_config)
            if proxy_url:
                current_opts['proxy'] = proxy_url
                logger.info(f"Trying {url} with proxy {i+1}/{len(all_configs)}: {proxy_url}")
                result = operation_func(current_opts, *args, **kwargs)
                
                if result is not None:
                    logger.info(f"Success with proxy {i+1}/{len(all_configs)}: {proxy_url}")
                    return result
                else:
                    logger.warning(f"Operation returned None with proxy {i+1}/{len(all_configs)}: {proxy_url}")
            else:
                logger.warning(f"Failed to build proxy URL for config {i+1}/{len(all_configs)}: {proxy_config}")
                
        except Exception as e:
            logger.warning(f"Failed with proxy {i+1}/{len(all_configs)} ({proxy_url}): {e}")
            continue
    
    # Try without proxy as last resort
    try:
        logger.info(f"All proxies failed for {url}, trying without proxy")
        current_opts = ytdl_opts.copy()
        if 'proxy' in current_opts:
            del current_opts['proxy']
        return operation_func(current_opts, *args, **kwargs)
    except Exception as e:
        logger.error(f"Failed without proxy for {url}: {e}")
        return None




def get_all_proxy_configs():
    """All real proxies from the config (the "X.X.X.X" placeholders are skipped)"""
    return [c for c in (get_proxy_config(), get_proxy_2_config()) if c]

def extract_domain_from_url(url):
    """Extract domain from URL, handling subdomains properly"""
    if '://' in url:
        domain = url.split('://')[1].split('/')[0]
    else:
        domain = url.split('/')[0]
    
    # Remove www. prefix if present
    if domain.startswith('www.'):
        domain = domain[4:]
    
    return domain

def is_domain_in_list(domain, domain_list):
    """Check if domain or any of its subdomains match entries in domain_list"""
    if not domain_list:
        return False
    
    # Direct match
    if domain in domain_list:
        return True
    
    # Check if any domain in the list is a subdomain of the current domain
    for listed_domain in domain_list:
        if domain.endswith('.' + listed_domain):
            return True
    
    return False

def select_proxy_for_domain(url):
    """Select appropriate proxy for domain based on PROXY_DOMAINS and PROXY_2_DOMAINS"""
    from CONFIG.domains import DomainsConfig
    
    domain = extract_domain_from_url(url)
    
    logger.info(f"select_proxy_for_domain: URL={url}, extracted_domain={domain}")
    logger.info(f"PROXY_2_DOMAINS: {getattr(DomainsConfig, 'PROXY_2_DOMAINS', 'NOT_FOUND')}")
    logger.info(f"PROXY_DOMAINS: {getattr(DomainsConfig, 'PROXY_DOMAINS', 'NOT_FOUND')}")
    
    # Check PROXY_2_DOMAINS first
    if hasattr(DomainsConfig, 'PROXY_2_DOMAINS') and DomainsConfig.PROXY_2_DOMAINS:
        if is_domain_in_list(domain, DomainsConfig.PROXY_2_DOMAINS):
            logger.info(f"Domain {domain} found in PROXY_2_DOMAINS, using proxy 2")
            return get_proxy_2_config()
    
    # Check PROXY_DOMAINS
    if hasattr(DomainsConfig, 'PROXY_DOMAINS') and DomainsConfig.PROXY_DOMAINS:
        if is_domain_in_list(domain, DomainsConfig.PROXY_DOMAINS):
            logger.info(f"Domain {domain} found in PROXY_DOMAINS, using proxy 1")
            return get_proxy_config()
    
    logger.info(f"Domain {domain} not found in any proxy domain lists")
    return None

def select_proxy_for_user():
    """Select proxy for user based on PROXY_SELECT method"""
    from CONFIG.config import Config
    import random
    
    configs = get_all_proxy_configs()
    if not configs:
        return None
    
    if len(configs) == 1:
        return configs[0]
    
    # Select method based on PROXY_SELECT
    if hasattr(Config, 'PROXY_SELECT') and Config.PROXY_SELECT == "random":
        return random.choice(configs)
    else:  # default to round_robin
        # Simple round-robin using a global counter
        if not hasattr(select_proxy_for_user, 'counter'):
            select_proxy_for_user.counter = 0
        selected = configs[select_proxy_for_user.counter % len(configs)]
        select_proxy_for_user.counter += 1
        return selected

def add_proxy_to_gallery_dl_config(config: dict, url: str, user_id: int = None) -> dict:
    """Add proxy to gallery-dl config if proxy is enabled for user or domain requires it"""
    logger.info(f"add_proxy_to_gallery_dl_config called: user_id={user_id}, url={url}")
    
    # Priority 1: Check if user has proxy enabled (/proxy on)
    if user_id:
        try:
            from COMMANDS.proxy_cmd import is_proxy_enabled
            proxy_enabled = is_proxy_enabled(user_id)
            logger.info(f"User {user_id} proxy enabled: {proxy_enabled}")
            if proxy_enabled:
                # Use round-robin/random selection for user proxy
                proxy_config = select_proxy_for_user()
                if proxy_config:
                    proxy_url = build_proxy_url(proxy_config)
                    if proxy_url:
                        config['extractor']['proxy'] = proxy_url
                        logger.info(f"Added user proxy for {user_id}: {proxy_url}")
                        return config
        except Exception as e:
            logger.warning(f"Error checking proxy for user {user_id}: {e}")
            pass
    
    # Priority 2: Check if domain requires specific proxy (only if user proxy is OFF)
    logger.info(f"Checking domain-specific proxy for {url}")
    proxy_config = select_proxy_for_domain(url)
    if proxy_config:
        proxy_url = build_proxy_url(proxy_config)
        if proxy_url:
            config['extractor']['proxy'] = proxy_url
            logger.info(f"Added domain-specific proxy for {url}: {proxy_url}")
        else:
            logger.warning(f"Failed to build proxy URL from config: {proxy_config}")
    else:
        logger.info(f"No domain-specific proxy found for {url}")
    
    return config
