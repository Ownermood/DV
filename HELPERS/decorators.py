# Decorators for automatic app usage
from functools import wraps
import os
# ####################################################################################
# Decorators for bot functionality
# ####################################################################################

from HELPERS.app_instance import get_app
from HELPERS.logger import logger
from HELPERS.safe_messeger import safe_send_message
from CONFIG.messages import Messages, safe_get_messages

# Get app instance
app = get_app()

# eternal reply-keyboard and reliable work with files
reply_keyboard_msg_ids = {}  # user_id: message_id

def get_main_reply_keyboard(mode="2x3"):
    messages = safe_get_messages(None)
    """Function for permanent reply-keyboard"""
    from pyrogram.types import ReplyKeyboardMarkup
    
    if mode == "1x3":
        keyboard = [
            ["/clean", "/cookie", "/settings"]
        ]
    elif mode == "FULL":
        keyboard = [
            [messages.CLEAN_EMOJI, messages.COOKIE_EMOJI, messages.SETTINGS_EMOJI, messages.PROXY_EMOJI, messages.IMAGE_EMOJI, messages.SEARCH_EMOJI, messages.ARGS_EMOJI],
            [messages.VIDEO_EMOJI, messages.USAGE_EMOJI, messages.SPLIT_EMOJI, messages.AUDIO_EMOJI, messages.SUBTITLE_EMOJI, messages.LANGUAGE_EMOJI, messages.NSFW_EMOJI],
            [messages.TAG_EMOJI, messages.HELP_EMOJI, messages.LIST_EMOJI, messages.PLAY_EMOJI, messages.KEYBOARD_EMOJI, messages.LINK_EMOJI, "🧾"]
        ]
    else:  # 2x3 mode (default)
        keyboard = [
            ["/clean", "/cookie", "/settings"],
            ["/playlist", "/search", "/help"]
        ]
    
    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
        one_time_keyboard=False
    )


# Remove conflicting on_message from decorators.py
# It should live only in handler_registry.py

_keyboard_cleared = set()   # users whose old reply keyboard was removed in this run


def clear_old_reply_keyboard(user_id) -> None:
    """Remove the old /clean /cookie /settings reply keyboard once per user (no visible message stays)."""
    if not user_id or user_id in _keyboard_cleared or int(user_id) < 0:
        return
    _keyboard_cleared.add(user_id)
    try:
        from pyrogram.types import ReplyKeyboardRemove
        m = app.send_message(user_id, "\u2063", reply_markup=ReplyKeyboardRemove())
        app.delete_messages(user_id, [m.id])
        old = reply_keyboard_msg_ids.pop(user_id, None)
        if old:
            app.delete_messages(user_id, [old])
    except Exception as e:
        logger.info(f"Could not clear reply keyboard for {user_id}: {e}")


def reply_with_keyboard(func):
    """The bot no longer shows a reply keyboard: this only removes the old one (once per user)."""
    def wrapper(*args, **kwargs):
        result = func(*args, **kwargs)
        user_id = None
        if 'message' in kwargs:
            user_id = getattr(kwargs['message'].chat, 'id', None)
        elif len(args) > 0 and hasattr(args[0], 'chat'):
            user_id = getattr(args[0].chat, 'id', None)
        elif len(args) > 1 and hasattr(args[1], 'chat'):
            user_id = getattr(args[1].chat, 'id', None)
        clear_old_reply_keyboard(user_id)
        return result

    return wrapper


def _extract_message_arg(args, kwargs):
    """Return best-effort message-like object from handler args."""
    for obj in list(args) + list(kwargs.values()):
        if hasattr(obj, "chat"):
            return obj
        if hasattr(obj, "message") and getattr(obj, "message", None):
            return obj.message
    return None


def _format_handler_context(func_name, message_obj):
    chat = getattr(message_obj, "chat", None) if message_obj else None
    from_user = getattr(message_obj, "from_user", None) if message_obj else None
    chat_id = getattr(chat, "id", None)
    chat_type = getattr(chat, "type", None)
    from_id = getattr(from_user, "id", None)
    text = None
    for attr in ("text", "caption", "data"):
        value = getattr(message_obj, attr, None) if message_obj else None
        if value:
            text = value
            break
    if isinstance(text, str) and len(text) > 160:
        text = text[:157] + "..."
    return f"{func_name} chat={chat_id} type={chat_type} from={from_id} text={text!r}"


def background_handler(func=None, *, label=None):
    """
    Decorator that offloads a handler into a background ThreadPoolExecutor
    and logs its lifecycle so long-running jobs don't block Pyrogram updates.
    """
    if func is None:
        return lambda f: background_handler(f, label=label)

    @wraps(func)
    def wrapper(*args, **kwargs):
        message_obj = _extract_message_arg(args, kwargs)
        context = _format_handler_context(label or func.__name__, message_obj)
        logger.info(f"[INBOUND] {context}")
        try:
            logger.info(f"[HANDLER-START] {context}")
            result = func(*args, **kwargs)
            logger.info(f"[HANDLER-DONE] {context}")
            return result
        except Exception:
            logger.exception(f"[HANDLER-CRASH] {context}")
            raise

    return wrapper

    
