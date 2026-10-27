# Subtitles command
import yt_dlp
import time
import random
from HELPERS.app_instance import get_app
from HELPERS.filesystem_hlp import create_directory
from HELPERS.decorators import reply_with_keyboard, background_handler
from HELPERS.logger import logger, send_to_logger
from HELPERS.limitter import is_user_in_channel
import math
from pyrogram import filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from CONFIG.config import Config
from CONFIG.messages import safe_get_messages
from CONFIG.logger_msg import LoggerMsg
import os
import glob

# Get app instance for decorators
app = get_app()

_subs_check_cache = globals().get('_subs_check_cache', {})
_LAST_TIMEDTEXT_TS = globals().get('_LAST_TIMEDTEXT_TS', 0.0)

# Per-session helpers to manage subtitle cache for a specific user+URL
def clear_subs_cache_for(user_id: int, url: str) -> int:
    """Clear subtitle availability cache entries for a specific user and URL.
    Returns number of removed entries."""
    global _subs_check_cache
    try:
        keys = list(_subs_check_cache.keys())
        removed = 0
        prefix = f"{url}_{user_id}"
        for k in keys:
            if isinstance(k, str) and k.startswith(prefix):
                _subs_check_cache.pop(k, None)
                removed += 1
        return removed
    except Exception as e:
        logger.debug(f"clear_subs_cache_for error: {e}")
        return 0

def get_or_compute_subs_langs(user_id: int, url: str):
    """Return (normal_langs, auto_langs) for URL, preferring per-session cache.
    Falls back to computing via yt-dlp once and persists to per-session cache file."""
    try:
        # Try session cache file first (managed by Always Ask)
        try:
            from DOWN_AND_UP.dl_state import load_subs_langs_cache, save_subs_langs_cache
            cached = load_subs_langs_cache(user_id, url)
        except Exception:
            cached = None
        if cached and isinstance(cached, tuple) and len(cached) == 2:
            normal, auto = cached
            if (normal or auto):
                return list(normal or []), list(auto or [])

        # Otherwise compute once and persist
        normal = get_available_subs_languages(url, user_id, auto_only=False)
        auto   = get_available_subs_languages(url, user_id, auto_only=True)
        try:
            from DOWN_AND_UP.dl_state import save_subs_langs_cache
            save_subs_langs_cache(user_id, url, normal, auto)
        except Exception:
            pass
        return list(normal or []), list(auto or [])
    except Exception as e:
        logger.debug(f"get_or_compute_subs_langs error: {e}")
        return [], []

# Dictionary of languages with their emoji flags and native names
LANGUAGES = {
    "ar": {"flag": "🇸🇦", "name": "العربية"},
    "be": {"flag": "🇧🇾", "name": "Belarusian"},
    "bg": {"flag": "🇧🇬", "name": "Bulgarian"},
    "bn": {"flag": "🇧🇩", "name": "বাংলা"},
    "cs": {"flag": "🇨🇿", "name": "Čeština"},
    "da": {"flag": "🇩🇰", "name": "Dansk"},
    "de": {"flag": "🇩🇪", "name": "Deutsch"},
    "el": {"flag": "🇬🇷", "name": "Ελληνικά"},
    "en": {"flag": "🇬🇧", "name": "English"},
    "en-US": {"flag": "🇺🇸", "name": "English (US)"},
    "en-GB": {"flag": "🇬🇧", "name": "English (UK)"},
    "es": {"flag": "🇪🇸", "name": "Español"},
    "es-419": {"flag": "🇲🇽", "name": "Español (Latinoamérica)"},
    "et": {"flag": "🇪🇪", "name": "Eesti"},
    "fa": {"flag": "🇮🇷", "name": "فارسی"},
    "fi": {"flag": "🇫🇮", "name": "Suomi"},
    "fr": {"flag": "🇫🇷", "name": "Français"},
    "he": {"flag": "🇮🇱", "name": "עברית"},
    "hi": {"flag": "🇮🇳", "name": "हिन्दी"},
    "hr": {"flag": "🇭🇷", "name": "Hrvatski"},
    "hu": {"flag": "🇭🇺", "name": "Magyar"},
    "hy": {"flag": "🇦🇲", "name": "Հայերեն"},
    "id": {"flag": "🇮🇩", "name": "Bahasa Indonesia"},
    "it": {"flag": "🇮🇹", "name": "Italiano"},
    "ja": {"flag": "🇯🇵", "name": "日本語"},
    "kk": {"flag": "🇰🇿", "name": "Kazakh"},
    "ko": {"flag": "🇰🇷", "name": "한국어"},
    "lt": {"flag": "🇱🇹", "name": "Lietuvių"},
    "lv": {"flag": "🇱🇻", "name": "Latviešu"},
    "nl": {"flag": "🇳🇱", "name": "Nederlands"},
    "no": {"flag": "🇳🇴", "name": "Norsk"},
    "pl": {"flag": "🇵🇱", "name": "Polski"},
    "pt": {"flag": "🇵🇹", "name": "Português"},
    "pt-BR": {"flag": "🇧🇷", "name": "Português (Brasil)"},
    "ro": {"flag": "🇷🇴", "name": "Română"},
    "ru": {"flag": "🇷🇺", "name": "Russian"},
    "sk": {"flag": "🇸🇰", "name": "Slovenčina"},
    "sl": {"flag": "🇸🇮", "name": "Slovenščina"},
    "sr": {"flag": "🇷🇸", "name": "Serbian"},
    "sv": {"flag": "🇸🇪", "name": "Svenska"},
    "th": {"flag": "🇹🇭", "name": "ไทย"},
    "tr": {"flag": "🇹🇷", "name": "Türkçe"},
    "uk": {"flag": "🇺🇦", "name": "Ukrainian"},
    "vi": {"flag": "🇻🇳", "name": "Tiếng Việt"},
    "zh": {"flag": "🇨🇳", "name": "中文"},
    "zh-Hans": {"flag": "🇨🇳", "name": "中文(简体)"},
    "zh-Hant": {"flag": "🇹🇼", "name": "中文(繁體)"},
    # Additional YouTube-supported languages
    "te": {"flag": "🇮🇳", "name": "తెలుగు"},
    "ta": {"flag": "🇮🇳", "name": "தமிழ்"},
    "mr": {"flag": "🇮🇳", "name": "मराठी"},
    "kn": {"flag": "🇮🇳", "name": "ಕನ್ನಡ"},
    "ml": {"flag": "🇮🇳", "name": "മലയാളം"},
    "gu": {"flag": "🇮🇳", "name": "ગુજરાતી"},
    "pa": {"flag": "🇮🇳", "name": "ਪੰਜਾਬੀ"},
    "ur": {"flag": "🇵🇰", "name": "اردو"},
    "ne": {"flag": "🇳🇵", "name": "नेपाली"},
    "si": {"flag": "🇱🇰", "name": "සිංහල"},
    "my": {"flag": "🇲🇲", "name": "မြန်မာ"},
    "km": {"flag": "🇰🇭", "name": "ភាសាខ្មែរ"},
    "lo": {"flag": "🇱🇦", "name": "ລາວ"},
    "ms": {"flag": "🇲🇾", "name": "Bahasa Melayu"},
    "fil": {"flag": "🇵🇭", "name": "Filipino"},
    "am": {"flag": "🇪🇹", "name": "አማርኛ"},
    "az": {"flag": "🇦🇿", "name": "Azərbaycan"},
    "ka": {"flag": "🇬🇪", "name": "ქართული"},
    "ky": {"flag": "🇰🇬", "name": "Kyrgyz"},
    "uz": {"flag": "🇺🇿", "name": "Oʻzbekcha"},
    "tg": {"flag": "🇹🇯", "name": "Tajik"},
    "tk": {"flag": "🇹🇲", "name": "Türkmen"},
    "mn": {"flag": "🇲🇳", "name": "Mongolian"},
    "ps": {"flag": "🇦🇫", "name": "پښتو"},
    "or": {"flag": "🇮🇳", "name": "ଓଡ଼ିଆ"},
    "as": {"flag": "🇮🇳", "name": "অসমীয়া"},
    "ca": {"flag": "🇪🇸", "name": "Català"},
    "gl": {"flag": "🇪🇸", "name": "Galego"},
    "eu": {"flag": "🇪🇸", "name": "Euskara"},
    "af": {"flag": "🇿🇦", "name": "Afrikaans"},
    "sq": {"flag": "🇦🇱", "name": "Shqip"},
    "mk": {"flag": "🇲🇰", "name": "Macedonian"},
    "bs": {"flag": "🇧🇦", "name": "Bosanski"},
    "is": {"flag": "🇮🇸", "name": "Íslenska"},
    "ga": {"flag": "🇮🇪", "name": "Gaeilge"},
    "cy": {"flag": "🇬🇧", "name": "Cymraeg"},
    "gd": {"flag": "🇬🇧", "name": "Gàidhlig"},
    "lb": {"flag": "🇱🇺", "name": "Lëtzebuergesch"},
    "mt": {"flag": "🇲🇹", "name": "Malti"},
    "sw": {"flag": "🇰🇪", "name": "Kiswahili"},
    "zu": {"flag": "🇿🇦", "name": "isiZulu"},
    "xh": {"flag": "🇿🇦", "name": "isiXhosa"},
    "ha": {"flag": "🇳🇬", "name": "Hausa"},
    "yo": {"flag": "🇳🇬", "name": "Yorùbá"},
    "ig": {"flag": "🇳🇬", "name": "Igbo"}
}


# Fallback flag for unknown/region languages
DEFAULT_FLAG = "🌐"

# Fallback flags for many 2-letter language codes not present in LANGUAGES
LANG_FALLBACK_FLAGS = {
    "aa": "🇩🇯",  # Afar → Djibouti
    "ab": "🇬🇪",  # Abkhaz → Georgia
    "ak": "🇬🇭",  # Akan → Ghana
    "am": "🇪🇹",
    "as": "🇮🇳",
    "ay": "🇧🇴",
    "ba": "🇷🇺",
    "bho": "🇮🇳",
    "bo": "🇨🇳",
    "br": "🇫🇷",
    "ceb": "🇵🇭",
    "co": "🇫🇷",
    "crs": "🇸🇨",
    "cy": "🇬🇧",
    "da": "🇩🇰",
    "dz": "🇧🇹",
    "gd": "🇬🇧",
    "gv": "🇮🇲",
    "ha": "🇳🇬",
    "haw": "🇺🇸",
    "hmn": "🌐",
    "ht": "🇭🇹",
    "ig": "🇳🇬",
    "iu": "🇨🇦",
    "jv": "🇮🇩",
    "kk": "🇰🇿",
    "kl": "🇬🇱",
    "km": "🇰🇭",
    "kn": "🇮🇳",
    "ky": "🇰🇬",
    "lb": "🇱🇺",
    "lg": "🇺🇬",
    "ln": "🇨🇩",
    "lo": "🇱🇦",
    "lt": "🇱🇹",
    "lv": "🇱🇻",
    "mk": "🇲🇰",
    "mn": "🇲🇳",
    "mt": "🇲🇹",
    "ne": "🇳🇵",
    "ny": "🇲🇼",
    "oc": "🇫🇷",
    "om": "🇪🇹",
    "or": "🇮🇳",
    "os": "🇷🇺",
    "pa": "🇮🇳",
    "ps": "🇦🇫",
    "qu": "🇵🇪",
    "rn": "🇧🇮",
    "rw": "🇷🇼",
    "sg": "🇨🇫",
    "si": "🇱🇰",
    "sm": "🇼🇸",
    "sn": "🇿🇼",
    "so": "🇸🇴",
    "sr": "🇷🇸",
    "ss": "🇿🇦",
    "st": "🇱🇸",
    "su": "🇮🇩",
    "ta": "🇮🇳",
    "te": "🇮🇳",
    "tg": "🇹🇯",
    "ti": "🇪🇷",
    "tk": "🇹🇲",
    "tn": "🇧🇼",
    "to": "🇹🇴",
    "ts": "🇿🇦",
    "tt": "🇷🇺",
    "ug": "🇨🇳",
    "ur": "🇵🇰",
    "uz": "🇺🇿",
    "ve": "🇿🇦",
    "vi": "🇻🇳",
    "wo": "🇸🇳",
    "xh": "🇿🇦",
    "yi": "🇺🇸",
    "yo": "🇳🇬",
    "zu": "🇿🇦",
}

def get_flag(lang_code: str, use_second_part: bool = False) -> str:
    """
    Get flag for language code.
    use_second_part: True for SUBS (use second part like DE from de-DE), False for DUBS (use first part like de from de-DE)
    """
    if lang_code in LANGUAGES:
        return LANGUAGES[lang_code]["flag"]
    
    # For SUBS: use second part (e.g., DE from de-DE)
    if use_second_part and '-' in lang_code:
        second_part = lang_code.split('-')[1]
        if second_part in LANGUAGES:
            return LANGUAGES[second_part]["flag"]
        # Try common country codes
        country_flags = {
            'DE': '🇩🇪', 'FR': '🇫🇷', 'US': '🇺🇸', 'GB': '🇬🇧', 'IT': '🇮🇹', 'BR': '🇧🇷',
            'ES': '🇪🇸', 'RU': '🇷🇺', 'CN': '🇨🇳', 'JP': '🇯🇵', 'KR': '🇰🇷', 'IN': '🇮🇳'
        }
        if second_part in country_flags:
            return country_flags[second_part]
    
    # For DUBS: use first part (e.g., de from de-DE)
    if not use_second_part and '-' in lang_code:
        first_part = lang_code.split('-')[0]
        if first_part in LANGUAGES:
            return LANGUAGES[first_part]["flag"]
    
    # Try fallback mapping directly
    base_try = lang_code.split('-')[0]
    if base_try in LANG_FALLBACK_FLAGS:
        return LANG_FALLBACK_FLAGS[base_try]

    # Try base code
    if '-' in lang_code:
        base = lang_code.split('-')[0]
        if base in LANGUAGES:
            return LANGUAGES[base]["flag"]
    
    return DEFAULT_FLAG


#ITEMS_PER_PAGE = 10  # Number of languages per page

#############################################################################################################################



@app.on_message(filters.command("subs") & filters.private)
@reply_with_keyboard
@background_handler(label="subs_command")
def subs_command(app, message):
    messages = safe_get_messages(message.chat.id)
    """Handle /subs command - show language selection menu"""
    user_id = message.from_user.id
    if int(user_id) not in Config.ADMIN and not is_user_in_channel(app, message):
        return


    # Fast args: /subs on|off|ru|ru auto  -> Various subtitle settings
    parts = (message.text or "").split()
    if len(parts) >= 2:
        arg = parts[1].lower()
        
        # /subs off
        if arg == "off":
            save_subs_always_ask(user_id, False)
            save_user_subs_language(user_id, "OFF")
            from HELPERS.safe_messeger import safe_send_message
            safe_send_message(user_id, safe_get_messages(user_id).SUBS_DISABLED_MSG, message=message)
            send_to_logger(message, safe_get_messages(user_id).SUBS_DISABLED_LOG_MSG.format(arg=arg))
            return
        
        # /subs on
        elif arg == "on":
            save_subs_always_ask(user_id, True)
            from HELPERS.safe_messeger import safe_send_message
            safe_send_message(user_id, safe_get_messages(user_id).SUBS_ALWAYS_ASK_ENABLED_MSG, message=message)
            send_to_logger(message, safe_get_messages(user_id).SUBS_ALWAYS_ASK_ENABLED_LOG_MSG.format(arg=arg))
            return
        
        # /subs ru (language code)
        elif arg in LANGUAGES:
            save_user_subs_language(user_id, arg)
            lang_info = LANGUAGES[arg]
            from HELPERS.safe_messeger import safe_send_message
            safe_send_message(user_id, safe_get_messages(user_id).SUBS_LANGUAGE_SET_MSG.format(flag=lang_info['flag'], name=lang_info['name']), message=message)
            send_to_logger(message, safe_get_messages(user_id).SUBS_LANGUAGE_SET_LOG_MSG.format(arg=arg))
            return
        
        # /subs ru auto (language + auto mode)
        elif len(parts) >= 3 and parts[2].lower() == "auto" and arg in LANGUAGES:
            save_user_subs_language(user_id, arg)
            save_user_subs_auto_mode(user_id, True)
            lang_info = LANGUAGES[arg]
            from HELPERS.safe_messeger import safe_send_message
            safe_send_message(user_id, safe_get_messages(user_id).SUBS_LANGUAGE_AUTO_SET_MSG.format(flag=lang_info['flag'], name=lang_info['name']), message=message)
            send_to_logger(message, safe_get_messages(user_id).SUBS_LANGUAGE_AUTO_SET_LOG_MSG.format(arg=arg))
            return
        
        # Invalid argument
        else:
            from HELPERS.safe_messeger import safe_send_message
            safe_send_message(user_id, 
                safe_get_messages(user_id).SUBS_INVALID_ARGUMENT_MSG +
                safe_get_messages(user_id).SUBS_VALID_OPTIONS_MSG + "\n" +
                safe_get_messages(user_id).SUBS_DISABLE_COMMAND_MSG +
                safe_get_messages(user_id).SUBS_ENABLE_ASK_MODE_MSG +
                safe_get_messages(user_id).SUBS_SET_LANGUAGE_MSG +
                safe_get_messages(user_id).SUBS_SET_LANGUAGE_AUTO_MSG +
                safe_get_messages(user_id).SUBS_EXAMPLE_AUTO_MSG,
                message=message
            )
            return


    # Enable AUTO/TRANS by default if not enabled before
    if not get_user_subs_auto_mode(user_id):
        save_user_subs_auto_mode(user_id, True)

    current_lang = get_user_subs_language(user_id)
    auto_mode = get_user_subs_auto_mode(user_id)

    # Create status text
    if current_lang == "OFF" or current_lang is None:
        status_text = safe_get_messages(user_id).SUBS_DISABLED_STATUS_MSG
    else:
        lang_info = LANGUAGES.get(current_lang, {"name": current_lang, "flag": "🌐"})
        auto_text = safe_get_messages(user_id).SUBS_AUTO_SUBS_TEXT if auto_mode else ""
        status_text = safe_get_messages(user_id).SUBS_SELECTED_LANGUAGE_MSG.format(flag=lang_info['flag'], name=lang_info['name'], auto_text=auto_text)

    from HELPERS.safe_messeger import safe_send_message
    safe_send_message(
        message.chat.id,
        safe_get_messages(user_id).SUBS_SETTINGS_MENU_MSG.format(status_text=status_text) +
        safe_get_messages(user_id).SUBS_WARNING_MSG +
        safe_get_messages(user_id).SUBS_LIMITATIONS_MSG +
        safe_get_messages(user_id).SUBS_QUICK_COMMANDS_MSG +
        safe_get_messages(user_id).SUBS_SETTINGS_ADDITIONAL_MSG +
        safe_get_messages(user_id).SUBS_SET_LANGUAGE_CODE_MSG +
        "• <code>/subs ru</code> - set language\n" +
        "• <code>/subs ru auto</code> - set language with AUTO/TRANS",
        reply_markup=get_language_keyboard(page=0, user_id=user_id, per_page_rows=8),
        parse_mode=enums.ParseMode.HTML,
        message=message
    )
    send_to_logger(message, safe_get_messages(user_id).SUBS_MENU_OPENED_LOG_MSG)










#############################################################################################

def clear_subs_check_cache():
    """Cleans the cache of subtitle checks"""
    global _subs_check_cache
    _subs_check_cache.clear()
    
    # Also clean up temporary language cache files from Always Ask menu
    try:
        # Find all user directories
        if os.path.exists("users"):
            for user_dir in os.listdir("users"):
                user_path = os.path.join("users", user_dir)
                if os.path.isdir(user_path):
                    # Remove ask_subs_*.json files
                    pattern = os.path.join(user_path, "ask_subs_*.json")
                    for cache_file in glob.glob(pattern):
                        try:
                            os.remove(cache_file)
                            logger.info(f"Removed temp subs cache: {cache_file}")
                        except Exception as e:
                            logger.debug(f"Failed to remove {cache_file}: {e}")
    except Exception as e:
        logger.debug(f"Error cleaning temp subs cache files: {e}")
    
    logger.info("Subs check cache cleared")

def check_subs_availability(url, user_id, quality_key=None, return_type=False):
    messages = safe_get_messages(user_id)
    """
    Checks the availability of subtitles for the language chosen by the user.
    If Return_type = True, returns "Normal", "Auto" or None.
    If Return_type = False, returns True/False (are there any saba at all).

    Also caching lists of languages for Normal and Auto.
    """
    try:
        cache_key = f"{url}_{user_id}_{return_type}"
        if cache_key in _subs_check_cache:
            return _subs_check_cache[cache_key]

        subs_lang = get_user_subs_language(user_id)
        if not subs_lang or subs_lang == "OFF":
            _subs_check_cache[cache_key] = False if not return_type else None
            return _subs_check_cache[cache_key]

        # We check the usual subtitles
        available_normal = get_available_subs_languages(url, user_id, auto_only=False)
        has_normal = lang_match(subs_lang, available_normal) is not None
        logger.info(f"check_subs_availability: normal subs - available={available_normal}, has_normal={has_normal}")

        # Check auto -associated subtitles
        available_auto = get_available_subs_languages(url, user_id, auto_only=True)
        has_auto = lang_match(subs_lang, available_auto) is not None
        logger.info(f"check_subs_availability: auto subs - available={available_auto}, has_auto={has_auto}")

        # Cash the found lists of languages separately
        _subs_check_cache[f"{url}_{user_id}_normal_langs"] = available_normal
        _subs_check_cache[f"{url}_{user_id}_auto_langs"] = available_auto

        # Determine the type or presence of sub
        if return_type:
            result = "normal" if has_normal else "auto" if has_auto else None
        else:
            result = has_normal or has_auto

        _subs_check_cache[cache_key] = result
        return result

    except Exception as e:
        logger.error(f"Error checking subtitle availability: {e}")
        return False if not return_type else None

def lang_match(user_lang, available_langs):
    # user_lang: for example, 'en', 'en -us', 'zh', 'pt'
    # AVAILABLE_LANGS: a list of all available languages, for example ['en -us', 'EN-GB', 'FR', 'PT-BR']
    logger.info(f"{LoggerMsg.SUBS_LANG_MATCH_USER_LANG_LOG_MSG}")
    
    if user_lang in available_langs:
        logger.info(f"lang_match: exact match found: {user_lang}")
        return user_lang
    
    # If the basic language is chosen, we look for any prefix with this
    if '-' not in user_lang:
        for lang in available_langs:
            if lang.startswith(user_lang + '-'):
                logger.info(f"lang_match: prefix match found: {lang} for {user_lang}")
                return lang
    
    # If a language with a hyphen is chosen, we are looking for a basic
    if '-' in user_lang:
        base = user_lang.split('-')[0]
        if base in available_langs:
            logger.info(f"lang_match: base match found: {base} for {user_lang}")
            return base
    
    # If the base is selected, we are looking for a duplicate code (ru-RU, EN-EN, etc.)
    if '-' not in user_lang:
        for lang in available_langs:
            if lang.lower() == f'{user_lang.lower()}-{user_lang.lower()}':
                logger.info(f"{LoggerMsg.SUBS_LANG_MATCH_DUPLICATE_LOG_MSG}")
                return lang
    
    # NEW: Look for auto-translated subtitles (e.g., 'ru-en' for user_lang='ru')
    if '-' not in user_lang:
        for lang in available_langs:
            if '-' in lang:
                parts = lang.split('-')
                if len(parts) == 2 and parts[0] == user_lang:
                    logger.info(f"{LoggerMsg.SUBS_LANG_MATCH_AUTO_TRANSLATED_LOG_MSG}")
                    return lang
    
    logger.info(f"lang_match: no match found for {user_lang}")
    return None

def ensure_utf8_srt(srt_path):
    """
    The ultimatum function for correcting any encodings and cracked.
    Forcibly transcodes the file in the UTF-8, trying all possible encodings.
    """
    import chardet
    
    if not os.path.isfile(srt_path):
        logger.error(f"File {srt_path} does not exist!")
        return None
    if os.path.getsize(srt_path) == 0:
        logger.error(f"File {srt_path} is empty!")
        return None

    # We read raw bytes
    with open(srt_path, 'rb') as f:
        raw = f.read()
        if not raw:
            logger.error(f"File {srt_path} is empty (raw)!")
            return None

    # Determine the encoding through Chardet
    result = chardet.detect(raw)
    detected_encoding = result['encoding'] or 'utf-8'
    confidence = result.get('confidence', 0)
    logger.info(f"File encoding detected {srt_path}: {detected_encoding} (confidence: {confidence:.2f})")

    # List of coding for forced testing (in priority)
    encodings_to_try = [
        'utf-8',
        'utf-8-sig',  # UTF-8 with BOM
        'cp1256',     # Arabic Windows
        'iso-8859-6', # Arabic ISO
        'cp1252',     # Western European
        'iso-8859-1', # Latin-1
        'cp1250',     # Central European
        'cp1251',     # Cyrillic
        'cp874',      # Thai
        'tis-620',    # Thai
        'big5',       # Traditional Chinese
        'gbk',        # Simplified Chinese
        'shift_jis',  # Japanese
        'euc-kr',     # Korean
        'utf-16',
        'utf-16le',
        'utf-16be',
    ]

    # Add the detected encoding to the beginning of the list
    if detected_encoding.lower() not in [enc.lower() for enc in encodings_to_try]:
        encodings_to_try.insert(0, detected_encoding)

    # We try to decode with each encoding
    decoded_text = None
    successful_encoding = None
    
    for encoding in encodings_to_try:
        try:
            decoded_text = raw.decode(encoding)
            successful_encoding = encoding
            logger.info(f"{LoggerMsg.SUBS_SUCCESSFULLY_DECODED_LOG_MSG}")
            break
        except (UnicodeDecodeError, LookupError) as e:
            logger.debug(f"{LoggerMsg.SUBS_FAILED_TO_DECODE_LOG_MSG}")
            continue

    # If no encoding has worked, we use Force Decode
    if decoded_text is None:
        logger.warning("All encodings did not work, I use force decode")
        decoded_text = raw.decode('utf-8', errors='replace')
        successful_encoding = 'utf-8 (force)'

    # We check if there are replacement symbols in the text (Unicode replacement char or '?')
    if '\ufffd' in decoded_text or '?' in decoded_text:
        logger.warning(f"{LoggerMsg.SUBS_REPLACEMENT_CHARACTERS_FOUND_LOG_MSG}")
        
        # We try again with other encodings, ignoring the already tried
        for encoding in ['cp1256', 'iso-8859-6', 'cp1252', 'utf-8-sig']:
            if encoding not in [enc.lower() for enc in encodings_to_try[:len(encodings_to_try)//2]]:
                try:
                    test_text = raw.decode(encoding)
                    if '' not in test_text and '?' not in test_text:
                        decoded_text = test_text
                        successful_encoding = encoding
                        logger.info(f"{LoggerMsg.SUBS_BEST_ENCODING_FOUND_LOG_MSG}")
                        break
                except:
                    continue

    # Record the result in UTF-8
    try:
        with open(srt_path, 'w', encoding='utf-8') as f:
            f.write(decoded_text)
        logger.info(f"{LoggerMsg.SUBS_FILE_SUCCESSFULLY_ENCODED_UTF8_LOG_MSG}")
        return srt_path
    except Exception as e:
        logger.error(f"Error writing file {srt_path}: {e}")
        return None

def get_user_subs_language(user_id):
    messages = safe_get_messages(user_id)
    """Get user's preferred subtitle language"""
    user_dir = os.path.join("users", str(user_id))
    subs_file = os.path.join(user_dir, "subs.txt")
    if os.path.exists(subs_file):
        with open(subs_file, "r", encoding="utf-8") as f:
            return f.read().strip()
    return None


def save_subs_always_ask(user_id, enabled: bool):
    messages = safe_get_messages(user_id)
    """Persist Always Ask mode for subtitles (controls 💬 SUBS button in Always Ask)."""
    user_dir = os.path.join("users", str(user_id))
    create_directory(user_dir)
    path = os.path.join(user_dir, "subs_always_ask.txt")
    if enabled:
        with open(path, "w", encoding="utf-8") as f:
            f.write("ON")
    else:
        if os.path.exists(path):
            os.remove(path)

def is_subs_always_ask(user_id) -> bool:
    user_dir = os.path.join("users", str(user_id))
    path = os.path.join(user_dir, "subs_always_ask.txt")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip().upper() == "ON"
        except Exception:
            return False
    return False

def save_user_subs_language(user_id, lang_code):
    messages = safe_get_messages(user_id)
    """Save user's subtitle language preference"""
    user_dir = os.path.join("users", str(user_id))
    create_directory(user_dir)
    subs_file = os.path.join(user_dir, "subs.txt")
    if lang_code in ["OFF", None]:
        if os.path.exists(subs_file):
            os.remove(subs_file)
        subs_auto_file = os.path.join(user_dir, "subs_auto.txt")
        if os.path.exists(subs_auto_file):
            os.remove(subs_auto_file)
        clear_subs_check_cache()
    else:
        with open(subs_file, "w", encoding="utf-8") as f:
            f.write(lang_code)
    clear_subs_check_cache()

def get_user_subs_auto_mode(user_id):
    messages = safe_get_messages(user_id)
    """Get user's AUTO mode setting for subtitles"""
    user_dir = os.path.join("users", str(user_id))
    auto_file = os.path.join(user_dir, "subs_auto.txt")
    if os.path.exists(auto_file):
        with open(auto_file, "r", encoding="utf-8") as f:
            return f.read().strip() == "ON"
    return False

def save_user_subs_auto_mode(user_id, auto_enabled):
    messages = safe_get_messages(user_id)
    """Save user's AUTO mode setting for subtitles"""
    user_dir = os.path.join("users", str(user_id))
    create_directory(user_dir)
    auto_file = os.path.join(user_dir, "subs_auto.txt")
    if auto_enabled:
        with open(auto_file, "w", encoding="utf-8") as f:
            f.write("ON")
    else:
        if os.path.exists(auto_file):
            os.remove(auto_file)
    clear_subs_check_cache()


def get_available_subs_languages(url, user_id=None, auto_only=False):
    messages = safe_get_messages(user_id)
    """Returns a list of available languages of subtitles. Circrats 429 and 'Requested Format ...'."""
    # import os, random, time, yt_dlp

    MAX_RETRIES = 1
    def backoff(i):  # short, because the listing itself usually does not meet the limits
        return (3, 5, 10)[min(i, 2)] + random.uniform(0, 2)

    def extract_info_with_cookies():
        base_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'noplaylist': True,
            'format': 'best',
            'ignore_no_formats_error': True,
            'sleep-requests': 2,
            'min_sleep_interval': 1,
            'max_sleep_interval': 3,
            'retries': 6,
            'extractor_retries': 3,
        }
        # cookies
        if user_id:
            cf = os.path.join("users", str(user_id), "cookie.txt")
            if os.path.exists(cf):
                base_opts['cookiefile'] = cf
        elif hasattr(Config, "COOKIE_FILE_PATH") and os.path.exists(Config.COOKIE_FILE_PATH):
            base_opts['cookiefile'] = Config.COOKIE_FILE_PATH

        last_info, used_client = {}, None
        for client in ('tv', None):  # Only try tv client since it always works
            opts = dict(base_opts)
            if client:
                opts['extractor_args'] = {'youtube': {'player_client': [client]}}
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
            except yt_dlp.utils.DownloadError as e:
                if 'Requested format is not available' in str(e):
                    continue
                raise
            if info.get('subtitles') or info.get('automatic_captions'):
                used_client = client or 'default'
                logger.info(f"{LoggerMsg.SUBS_YOUTUBE_PLAYER_CLIENT_RETURNED_CAPTIONS_LOG_MSG}")
                _subs_check_cache[f"{url}_{user_id}_client"] = used_client
                return info
            logger.info(f"{LoggerMsg.SUBS_PLAYER_CLIENT_NO_CAPTIONS_LOG_MSG}")
            last_info = info
        _subs_check_cache[f"{url}_{user_id}_client"] = used_client or 'default'
        return last_info

    def _list_via_timedtext(u: str):
        """Fallback: query YouTube timedtext list endpoint and split normal/auto (asr)."""
        try:
            import requests
            import re as _re
            # extract video id
            vid = None
            m = _re.search(r"[?&]v=([\w-]{11})", u)
            if m:
                vid = m.group(1)
            if not vid:
                m = _re.search(r"youtu\.be/([\w-]{11})", u)
                if m:
                    vid = m.group(1)
            if not vid:
                return [], []
            tt_url = f"https://www.youtube.com/api/timedtext?type=list&v={vid}"
            headers = {"User-Agent": "Mozilla/5.0"}
            r = requests.get(tt_url, headers=headers, timeout=15)
            if r.status_code != 200 or not r.text:
                return [], []
            normal, auto = [], []
            # Parse simple XML lines
            for m in _re.finditer(r'<track[^>]*lang_code="([^"]+)"[^>]*>', r.text):
                tag = m.group(0)
                code = m.group(1)
                if 'kind="asr"' in tag or "kind='asr'" in tag:
                    auto.append(code)
                else:
                    normal.append(code)
            return list(set(normal)), list(set(auto))
        except Exception as _e:
            logger.warning(f"timedtext list fallback failed: {_e}")
            return [], []

    for attempt in range(MAX_RETRIES):
        try:
            info = extract_info_with_cookies()
            normal = list(info.get('subtitles', {}).keys())
            auto   = list(info.get('automatic_captions', {}).keys())
            result = list(set(auto if auto_only else normal))
            logger.info(f"get_available_subs_languages: auto_only={auto_only}, result={result}")
            # Fallback to timedtext list if nothing found
            if not normal and not auto:
                tt_normal, tt_auto = _list_via_timedtext(url)
                if tt_normal or tt_auto:
                    logger.info(f"get_available_subs_languages: timedtext fallback normal={tt_normal}, auto={tt_auto}")
                    _subs_check_cache[f"{url}_{user_id}_normal_langs"] = tt_normal
                    _subs_check_cache[f"{url}_{user_id}_auto_langs"] = tt_auto
                    return tt_auto if auto_only else tt_normal
            return result

        except yt_dlp.utils.DownloadError as e:
            if "429" in str(e) and attempt < MAX_RETRIES - 1:
                delay = backoff(attempt)
                logger.warning(f"{LoggerMsg.SUBS_429_TOO_MANY_REQUESTS_SLEEP_LOG_MSG}")
                time.sleep(delay)
                continue
            logger.error(f"{LoggerMsg.SUBS_DOWNLOAD_ERROR_GETTING_SUBTITLES_LOG_MSG}")
            break
        except Exception as e:
            logger.error(f"{LoggerMsg.SUBS_UNEXPECTED_ERROR_GETTING_SUBTITLES_LOG_MSG}")
            break

    return []


def force_fix_arabic_encoding(srt_path: str, lang: str | None = None):
    """Forced transcoding Arab/Pers/Urdu/Hebrew Sabov in UTF-8."""
    target_langs = {'ar', 'fa', 'ur', 'ps', 'iw', 'he'}
    if lang is not None and lang not in target_langs:
        return srt_path
    if not os.path.exists(srt_path):
        return None

    try:
        with open(srt_path, 'rb') as f:
            raw = f.read()

        encodings = ['utf-8-sig', 'utf-8', 'cp1256', 'windows-1256', 'iso-8859-6', 'cp720', 'mac-arabic']
        best_text, best_enc, min_bad = None, None, float('inf')

        for enc in encodings:
            try:
                text = raw.decode(enc, errors='replace')
            except Exception:
                continue
            bad = text.count('?')  # simple heuristics
            if bad and bad < min_bad:
                min_bad = bad
                best_text = text
                best_enc = enc

        if best_text is None:
            best_text = raw.decode('utf-8', errors='replace')
            best_enc  = 'utf-8(force)'

        best_text = best_text.replace('\r\n', '\n').replace('\r', '\n').replace('\ufeff', '')
        with open(srt_path, 'w', encoding='utf-8') as f:
            f.write(best_text)

        logger.info(f"{LoggerMsg.SUBS_FORCE_FIX_ARABIC_ENCODING_LOG_MSG}")
        return srt_path
    except Exception as e:
        logger.error(f"{LoggerMsg.SUBS_FORCE_FIX_ARABIC_ENCODING_ERROR_LOG_MSG}")
        return None












def get_language_keyboard(page=0, user_id=None, langs_override=None, per_page_rows=8):
    messages = safe_get_messages(user_id)
    """Generate keyboard with language buttons in 3 columns. Supports paging and optional language override."""
    keyboard = []
    LANGS_PER_ROW = 3

    ROWS_PER_PAGE = per_page_rows  # default 8 -> 24 per page


    # We get all languages
    if langs_override is not None:
        # langs_override is list of codes
        all_langs = [(code, {"flag": get_flag(code), "name": code}) for code in langs_override]
    else:
        all_langs = list(LANGUAGES.items())
    total_languages = len(all_langs)
    total_pages = math.ceil(total_languages / (LANGS_PER_ROW * ROWS_PER_PAGE))

    # Cut for the current page
    start_idx = page * LANGS_PER_ROW * ROWS_PER_PAGE
    end_idx = start_idx + LANGS_PER_ROW * ROWS_PER_PAGE
    current_page_langs = all_langs[start_idx:end_idx]

    # Current language and auto-mode
    current_lang = get_user_subs_language(user_id) if user_id else None
    auto_mode = get_user_subs_auto_mode(user_id) if user_id else False

    # Form buttons 3 in a row
    for i in range(0, len(current_page_langs), LANGS_PER_ROW):
        row = []
        for j in range(LANGS_PER_ROW):
            if i + j < len(current_page_langs):
                lang_code, lang_info = current_page_langs[i + j]
                checkmark = safe_get_messages(user_id).SUBS_LANGUAGE_CHECKMARK_MSG if lang_code == current_lang else ""
                button_text = f"{checkmark}{lang_info.get('flag', get_flag(lang_code))} {lang_info.get('name', lang_code)}"
                row.append(InlineKeyboardButton(
                    button_text,
                    callback_data=f"subs_lang|{lang_code}"
                ))
        keyboard.append(row)

    # Navigation
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(safe_get_messages(user_id).SUBS_PREV_BUTTON_MSG, callback_data=f"subs_page|{page-1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(safe_get_messages(user_id).SUBTITLES_NEXT_BUTTON_MSG, callback_data=f"subs_page|{page+1}"))
    if nav_row:
        keyboard.append(nav_row)

    # Specialist. Options
    auto_emoji = safe_get_messages(user_id).SUBS_AUTO_EMOJI_MSG if auto_mode else safe_get_messages(user_id).SUBS_AUTO_EMOJI_INACTIVE_MSG
    keyboard.append([
        InlineKeyboardButton(safe_get_messages(user_id).SUBS_OFF_BUTTON_MSG, callback_data="subs_lang|OFF"),
        InlineKeyboardButton(f"{auto_emoji} {safe_get_messages(user_id).SUBS_AUTO_TRANS_BUTTON_MSG}", callback_data=f"subs_auto|toggle|{page}")

    ])
    # Always Ask option
    always_ask_enabled = is_subs_always_ask(user_id) if user_id else False
    always_ask_emoji = safe_get_messages(user_id).SUBS_ALWAYS_ASK_EMOJI_MSG if always_ask_enabled else safe_get_messages(user_id).SUBS_ALWAYS_ASK_EMOJI_INACTIVE_MSG
    keyboard.append([
        InlineKeyboardButton(f"{always_ask_emoji} {safe_get_messages(user_id).SUBS_ALWAYS_ASK_BUTTON_MSG}", callback_data=f"subs_always_ask|toggle|{page}")

    ])
    # Close button
    keyboard.append([
        InlineKeyboardButton(safe_get_messages(user_id).URL_EXTRACTOR_HELP_CLOSE_BUTTON_MSG, callback_data="subs_lang_close|close")
    ])

    return InlineKeyboardMarkup(keyboard)


######################################################
