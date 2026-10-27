import tldextract
import re
from urllib.parse import urlparse, parse_qs, unquote
# --- an auxiliary function for extracting a domain ---
def unwrap_redirect_url(url: str) -> str:
    """
    Unwrap common redirect links (Google, Facebook, etc.) by extracting the first
    http(s) URL from known query parameters like url, u, q, redirect, redir, target, to, dest, destination, r, s.

    Performs up to 2 unwrapping passes.
    """
    try:
        current = url
        for _ in range(2):
            parsed = urlparse(current)
            qs = parse_qs(parsed.query or "")
            candidate = None
            for key in [
                "url", "u", "q", "redirect", "redir", "target", "to", "dest", "destination", "r", "s"
            ]:
                values = qs.get(key)
                if not values:
                    continue
                for v in values:
                    v = unquote(v)
                    m = re.search(r"https?://[^\s]+", v)
                    if m:
                        candidate = m.group(0)
                        break
                if candidate:
                    break
            if candidate:
                current = candidate
                continue
            break
        return current
    except Exception:
        return url
from CONFIG.config import Config
from CONFIG.domains import DomainsConfig
from HELPERS.logger import logger

# --- global lists of domains and keywords ---
PORN_DOMAINS = set()
SUPPORTED_SITES = set()
PORN_KEYWORDS = set()

# --- loading lists at start ---
def load_domain_lists():
    global PORN_DOMAINS, SUPPORTED_SITES, PORN_KEYWORDS
    try:
        with open(Config.PORN_DOMAINS_FILE, 'r', encoding='utf-8', errors='ignore') as f:
            PORN_DOMAINS = set(line.strip().lower() for line in f if line.strip())
        logger.info(f"Loaded {len(PORN_DOMAINS)} domains from {Config.PORN_DOMAINS_FILE}. Example: {list(PORN_DOMAINS)[:5]}")
    except Exception as e:
        logger.error(f"Failed to load {Config.PORN_DOMAINS_FILE}: {e}")
        PORN_DOMAINS = set()
    try:
        with open(Config.PORN_KEYWORDS_FILE, 'r', encoding='utf-8', errors='ignore') as f:
            PORN_KEYWORDS = set(line.strip().lower() for line in f if line.strip())
        logger.info(f"Loaded {len(PORN_KEYWORDS)} keywords from {Config.PORN_KEYWORDS_FILE}. Example: {list(PORN_KEYWORDS)[:5]}")
    except Exception as e:
        logger.error(f"Failed to load {Config.PORN_KEYWORDS_FILE}: {e}")
        PORN_KEYWORDS = set()
    try:
        with open(Config.SUPPORTED_SITES_FILE, 'r', encoding='utf-8', errors='ignore') as f:
            SUPPORTED_SITES = set(line.strip().lower() for line in f if line.strip())
        logger.info(f"Loaded {len(SUPPORTED_SITES)} supported sites from {Config.SUPPORTED_SITES_FILE}. Example: {list(SUPPORTED_SITES)[:5]}")
    except Exception as e:
        logger.error(f"Failed to load {Config.SUPPORTED_SITES_FILE}: {e}")
        SUPPORTED_SITES = set()

load_domain_lists()

# --- an auxiliary function for extracting a domain ---
def extract_domain_parts(url):
    try:
        # Try to unwrap redirectors before extracting the domain
        unwrapped = unwrap_redirect_url(url)
        ext = tldextract.extract(unwrapped)
        # We collect the domain: Domain.suffix (for example, xvideos.com)
        if ext.domain and ext.suffix:
            full_domain = f"{ext.domain}.{ext.suffix}".lower()
            subdomain = ext.subdomain.lower() if ext.subdomain else ''
            # We get all the suffixes: xvideos.com, b.xvideos.com, a.b.xvideos.com
            parts = [full_domain]
            if subdomain:
                sub_parts = subdomain.split('.')
                for i in range(len(sub_parts)):
                    parts.append('.'.join(sub_parts[i:] + [full_domain]))
            return parts, ext.domain.lower()
        elif ext.domain:
            return [ext.domain.lower()], ext.domain.lower()
        else:
            return [url.lower()], url.lower()
    except Exception:
        # Fallback for URLs without a clear domain, e.g., "localhost"
        parsed = urlparse(url)
        if parsed.netloc:
             return [parsed.netloc.lower()], parsed.netloc.lower()
        return [url.lower()], url.lower()


# --- White list of domains that are not considered porn ---
# Now we take from config.py

def is_porn_domain(domain_parts):
    # If any suffix domain is on a whitelist, it is not porn
    for dom in domain_parts:
        if dom in Config.WHITELIST:
            return False
    # GREYLIST: exclude from domain list check entirely (keywords still apply via is_porn)
    for dom in domain_parts:
        if dom in Config.GREYLIST:
            return False
    # If any suffix domain is in the porn domains list, treat as porn
    for dom in domain_parts:
        if dom in PORN_DOMAINS:
            return True
    return False

# --- a new function for checking for porn ---
def is_porn(url, title, description, caption=None, tags=None):
    """
    Checks content for pornography by domain and keywords (word-boundary regex search)
    in title, description, caption, tags and URL. Domain whitelist has highest priority.
    White keywords list can override porn detection for false positive correction.
    URL keywords are checked with spaces replaced by underscores and dashes.
    Tags are checked with underscores treated as word separators.
    """
    # 1. Checking the domain (with redirect unwrapping)
    clean_url = unwrap_redirect_url(url).lower()
    domain_parts, _ = extract_domain_parts(clean_url)
    for dom in Config.WHITELIST:
        if dom in domain_parts:
            logger.info(f"is_porn: domain in WHITELIST: {dom}")
            return False
    if is_porn_domain(domain_parts):
        logger.info(f"is_porn: domain match: {domain_parts}")
        return True

    # 2. Preparation of the text
    title_lower       = title.lower()       if title       else ""
    description_lower = description.lower() if description else ""
    caption_lower     = caption.lower()     if caption     else ""
    # Process tags: replace underscores with spaces for keyword matching
    tags_lower = ""
    if tags:
        # Tags are space-separated, but keywords inside tags use underscores
        # Replace underscores with spaces for matching
        tags_lower = tags.lower().replace("_", " ")
    
    # 3. Prepare URL for keyword checking (replace spaces with underscores and dashes)
    url_lower = clean_url
    logger.debug(f"is_porn URL for keyword check: '{url_lower}'")
    
    if not (title_lower or description_lower or caption_lower or tags_lower or url_lower):
        logger.info("is_porn: all text fields and URL empty")
        return False

    # 4. We collect a single text for search (including URL and tags)
    combined = " ".join([title_lower, description_lower, caption_lower, tags_lower, url_lower])
    logger.debug(f"is_porn combined text: '{combined}'")
    logger.debug(f"is_porn keywords: {PORN_KEYWORDS}")

    # 5. Check for white keywords first (override porn detection)
    white_keywords = getattr(DomainsConfig, 'WHITE_KEYWORDS', [])
    if white_keywords:
        white_kws = [re.escape(kw.lower()) for kw in white_keywords if kw.strip()]
        if white_kws:
            white_pattern = re.compile(r"\b(" + "|".join(white_kws) + r")\b", flags=re.IGNORECASE)
            if white_pattern.search(combined):
                logger.info(f"is_porn: white keyword match found, content considered clean: {white_pattern.pattern}")
                return False

    # 6. Preparing regex patterns
    # For text fields we use word boundaries with escaped keywords
    text_kws = [re.escape(kw.lower()) for kw in PORN_KEYWORDS if kw.strip()]
    # For URL checks we need raw lowercase keywords to build custom delimiter-aware patterns
    url_kws = [kw.lower() for kw in PORN_KEYWORDS if kw.strip()]
    if not text_kws:
        # There is not a single valid key
        return False

    # 7. Check for keyword matches in text fields (with word boundaries)
    # Include tags in text fields check (tags already have underscores replaced with spaces)
    text_pattern = re.compile(r"\b(" + "|".join(text_kws) + r")\b", flags=re.IGNORECASE)
    text_to_check = " ".join([title_lower, description_lower, caption_lower, tags_lower])
    if text_pattern.search(text_to_check):
        logger.info(f"is_porn: keyword match in text fields (regex): {text_pattern.pattern}")
        return True

    # 8. Check for keyword matches in URL with delimiter-aware patterns
    # We only trigger when keyword is delimited on both sides by non-alphanumeric chars (e.g. _, -, symbols) or string edges.
    def _compile_url_keyword_regex(raw_keyword: str) -> re.Pattern:
        words = [re.escape(w) for w in raw_keyword.split() if w]
        if not words:
            # Fallback: compile something that never matches
            return re.compile(r"a^")
        # allow ANY non-alphanumeric delimiters between words inside URL
        joiner = r"[^A-Za-z0-9]+"
        core = joiner.join(words)
        # Require non-alphanumeric (or start/end) around the whole keyword
        return re.compile(rf"(?<![A-Za-z0-9])(?:{core})(?![A-Za-z0-9])", flags=re.IGNORECASE)

    for raw_kw in url_kws:
        url_pattern = _compile_url_keyword_regex(raw_kw)
        if url_pattern.search(url_lower):
            logger.info(f"is_porn: keyword match in URL with delimiters: {raw_kw}")
            return True

    logger.info("is_porn: no keyword matches found")
    return False




# --- runtime reload of config-driven lists and file caches ---
