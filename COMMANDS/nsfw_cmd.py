# /NSFW Command
import os
from pyrogram import filters, enums
from CONFIG.config import Config
from CONFIG.messages import Messages, safe_get_messages
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from HELPERS.app_instance import get_app
from HELPERS.filesystem_hlp import create_directory
from HELPERS.logger import send_to_logger, logger
from CONFIG.logger_msg import LoggerMsg
from HELPERS.safe_messeger import safe_send_message, safe_edit_message_text
from HELPERS.decorators import background_handler
from HELPERS.limitter import is_user_in_channel

# Get app instance for decorators
app = get_app()

# Like mediainfo: handle private here; groups are registered/wrapped in magic.py
@app.on_message(filters.command("nsfw"))
@background_handler(label="nsfw_command")
def nsfw_command(app, message):
    messages = safe_get_messages(message.chat.id)
    chat_id = message.chat.id
    chat_type = getattr(message.chat, "type", None)
    # Store setting per-chat: in groups/channels use chat_id (negative), in private use user id (== chat_id)
    user_id = getattr(message.from_user, "id", None) or chat_id
    storage_id = chat_id
    is_admin = int(user_id) in Config.ADMIN
    is_in_channel = is_user_in_channel(app, message)
    logger.info(LoggerMsg.NSFW_USER_REQUESTED_COMMAND_LOG_MSG.format(user_id=user_id))
    logger.info(LoggerMsg.NSFW_USER_IS_ADMIN_LOG_MSG.format(user_id=user_id, is_admin=is_admin))
    logger.info(LoggerMsg.NSFW_USER_IS_IN_CHANNEL_LOG_MSG.format(user_id=user_id, is_in_channel=is_in_channel))
    
    # In private chats: require subscription (like mediainfo). In allowed groups: bypass (wrapper in magic.py filters groups).
    if chat_type == enums.ChatType.PRIVATE:
        if int(user_id) not in Config.ADMIN and not is_user_in_channel(app, message):
            logger.info(f"[NSFW] User {user_id} access denied - not admin and not in channel")
            return
    
    logger.info(f"[NSFW] User {user_id} access granted")
    user_dir = os.path.join("users", str(storage_id))
    create_directory(user_dir)
    
    # Fast toggle via args: /nsfw on|off
    try:
        parts = (message.text or "").split()
        if len(parts) >= 2:
            arg = parts[1].lower()
            nsfw_file = os.path.join(user_dir, "nsfw_blur.txt")
            if arg in ("on", "off"):
                with open(nsfw_file, "w", encoding="utf-8") as f:
                    f.write("ON" if arg == "on" else "OFF")
                
                if arg == "on":
                    safe_send_message(chat_id, safe_get_messages(user_id).NSFW_ON_MSG, parse_mode=enums.ParseMode.HTML, message=message)
                else:
                    safe_send_message(chat_id, safe_get_messages(user_id).NSFW_OFF_MSG, parse_mode=enums.ParseMode.HTML, message=message)
                
                send_to_logger(message, safe_get_messages(user_id).NSFW_BLUR_SET_COMMAND_LOG_MSG.format(arg=arg))
                return
            else:
                safe_send_message(chat_id, safe_get_messages(user_id).NSFW_INVALID_MSG, parse_mode=enums.ParseMode.HTML, message=message)
                return
    except Exception as e:
        logger.error(f"Error processing nsfw command: {e}")
        pass
    
    # Show menu if no args provided
    # Check current setting to show proper status
    current_setting = is_nsfw_blur_enabled(storage_id)
    on_text = safe_get_messages(user_id).NSFW_ON_NO_BLUR_MSG if not current_setting else safe_get_messages(user_id).NSFW_ON_NO_BLUR_INACTIVE_MSG
    off_text = safe_get_messages(user_id).NSFW_OFF_BLUR_MSG if current_setting else safe_get_messages(user_id).NSFW_OFF_BLUR_INACTIVE_MSG
    
    buttons = [
        [InlineKeyboardButton(on_text, callback_data="nsfw_option|on"), InlineKeyboardButton(off_text, callback_data="nsfw_option|off")],
        [InlineKeyboardButton(safe_get_messages(user_id).URL_EXTRACTOR_HELP_CLOSE_BUTTON_MSG, callback_data="nsfw_option|close")],
    ]
    keyboard = InlineKeyboardMarkup(buttons)
    
    status_text = "currently blurred" if current_setting else "currently not blurred"
    safe_send_message(
        chat_id,
safe_get_messages(user_id).NSFW_BLUR_SETTINGS_TITLE_MSG.format(status=status_text),
        reply_markup=keyboard,
        parse_mode=enums.ParseMode.HTML,
        message=message
    )
    send_to_logger(message, safe_get_messages(user_id).NSFW_MENU_OPENED_LOG_MSG)




def is_nsfw_blur_enabled(user_id):
    messages = safe_get_messages(user_id)
    """
    Check if NSFW blur is enabled for user.
    Returns True if blur should be applied (default behavior).
    Returns False if blur should be disabled.
    """
    user_dir = os.path.join("users", str(user_id))
    nsfw_file = os.path.join(user_dir, "nsfw_blur.txt")
    if not os.path.exists(nsfw_file):
        return True  # Default: blur enabled
    
    try:
        with open(nsfw_file, "r", encoding="utf-8") as f:
            content = f.read().strip().upper()
            return content != "ON"  # If file contains "ON", blur is disabled
    except Exception:
        return True  # Default: blur enabled on error


def should_apply_spoiler(user_id, is_nsfw, is_private_chat):
    messages = safe_get_messages(user_id)
    """
    Determine if spoiler should be applied based on user settings and context.
    
    Args:
        user_id: User ID
        is_nsfw: Whether content is NSFW
        is_private_chat: Whether it's a private chat
    
    Returns:
        bool: True if spoiler should be applied
    """
    if not is_nsfw:
        return False
    
    # In groups, never apply spoiler for NSFW content
    if not is_private_chat:
        return False
    
    # In private chats, check user's blur setting
    return is_nsfw_blur_enabled(user_id)
