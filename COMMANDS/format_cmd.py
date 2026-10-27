
# Command /Format Handler
from pyrogram import filters
from CONFIG.config import Config
from CONFIG.messages import Messages, safe_get_messages
from CONFIG.logger_msg import LoggerMsg
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyParameters

from HELPERS.app_instance import get_app
from HELPERS.logger import send_to_logger, logger
from HELPERS.filesystem_hlp import create_directory
from HELPERS.limitter import is_user_in_channel
from HELPERS.safe_messeger import safe_send_message, safe_edit_message_text
from HELPERS.decorators import background_handler
from urllib.parse import urlparse
import os
import json
import re

# Session-scoped overrides (not persisted)
_SESSION_MKV_OVERRIDE = {}

# Per-user format preferences (persisted in users/<id>/format_prefs.json)
def _prefs_path(user_id):
    messages = safe_get_messages(user_id)
    return os.path.join("users", str(user_id), "format_prefs.json")

def _default_prefs():
    # codec: avc1 | av01 | vp9
    # mkv: True -> remux to mkv container, False -> default to mp4
    return {"codec": "avc1", "mkv": False}

def parse_quality_argument(quality_arg):
    """
    Parses quality argument and returns format for yt-dlp
    
    Args:
        quality_arg (str): Quality argument (e.g., "720", "720p", "4k", "8K")
        
    Returns:
        str: Format for yt-dlp
    """
    if not quality_arg:
        return "best"
    
    quality_arg = quality_arg.lower().strip()
    
    # Remove 'p' or 'P' if present
    if quality_arg.endswith('p'):
        quality_arg = quality_arg[:-1]
    
    # Special cases for 4K and 8K
    if quality_arg in ['4k', '4']:
        return "bv*[height<=2160]+ba/bv*[height<=2160]/bv+ba/best"
    elif quality_arg in ['8k', '8']:
        return "bv*[height<=4320]+ba/bv*[height<=4320]/bv+ba/best"
    
    # Check if this is a number from 1 to 10000
    try:
        quality_num = int(quality_arg)
        if 1 <= quality_num <= 10000:
            return f"bv*[height<={quality_num}]+ba/bv*[height<={quality_num}]/bv+ba/best"
        else:
            return "best"
    except ValueError:
        return "best"

def load_user_prefs(user_id):
    messages = safe_get_messages(user_id)
    try:
        path = _prefs_path(user_id)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Backward/forward compatibility
                if not isinstance(data, dict):
                    return _default_prefs()
                data.setdefault("codec", "avc1")
                # Backward compatibility: migrate from old 'webm' to new 'mkv' (default OFF)
                data.setdefault("mkv", False)
                return data
    except Exception:
        pass
    return _default_prefs()




def get_user_mkv_preference(user_id):
    messages = safe_get_messages(user_id)
    # Session override takes precedence
    key = str(user_id)
    if key in _SESSION_MKV_OVERRIDE:
        return bool(_SESSION_MKV_OVERRIDE[key])
    prefs = load_user_prefs(user_id)
    return bool(prefs.get("mkv", False))




# Get app instance for decorators
app = get_app()



# Callbackquery Handler for /Format Menu Selection

# Callback processor for codec selection


# Callback processor to close the message
# ####################################################################################
