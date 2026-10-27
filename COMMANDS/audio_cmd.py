"""MP3: "/audio <url>" (sent internally by the MP3 buttons) -> down_and_audio."""
import os

from pyrogram import enums
from pyrogram.types import ReplyParameters

from CONFIG.config import Config
from CONFIG.messages import safe_get_messages
from DOWN_AND_UP.down_and_audio import down_and_audio
from HELPERS.download_status import get_active_download
from HELPERS.filesystem_hlp import create_directory
from HELPERS.limitter import is_user_in_channel, check_playlist_range_limits
from HELPERS.logger import send_to_logger
from HELPERS.safe_messeger import safe_send_message
from URL_PARSERS.tags import extract_url_range_tags, save_user_tags


def audio_command_handler(app, message):
    user_id = message.chat.id
    if get_active_download(user_id):
        safe_send_message(user_id, safe_get_messages(user_id).AUDIO_WAIT_MSG, reply_parameters=ReplyParameters(message_id=message.id))
        return
    if int(user_id) not in Config.ADMIN and not is_user_in_channel(app, message):
        return
    user_dir = os.path.join("users", str(user_id))
    create_directory(user_dir)
    text = message.text
    # Range syntax normalization:
    # support "/audio 1-10 URL" -> convert to "/audio URL*1*10" for a single pipeline
    try:
        parts = (text or '').split()
        if len(parts) >= 3 and parts[0].lower().startswith('/audio'):
            import re
            if re.match(r"^\d+-\d*$", parts[1]):
                rng = parts[1]
                start_str, end_str = rng.split('-')
                start_val = int(start_str)
                end_val = int(end_str) if end_str != '' else None
                if start_val and start_val >= 1:
                    url_candidate = ' '.join(parts[2:]).strip()
                    if end_val is not None:
                        text = f"/audio {url_candidate}*{start_val}*{end_val}"
                    else:
                        text = f"/audio {url_candidate}*{start_val}*"
    except Exception:
        pass
    url, _, _, _, tags, tags_text, tag_error = extract_url_range_tags(text)
    if tag_error:
        wrong, example = tag_error
        error_msg = safe_get_messages(user_id).OTHER_TAG_ERROR_MSG.format(wrong=wrong, example=example)
        safe_send_message(user_id, error_msg, reply_parameters=ReplyParameters(message_id=message.id))
        from HELPERS.logger import log_error_to_channel
        log_error_to_channel(message, error_msg)
        return
    if not url:
        safe_send_message(user_id, safe_get_messages(user_id).AUDIO_HELP_MSG, parse_mode=enums.ParseMode.HTML,
                          reply_parameters=ReplyParameters(message_id=message.id))
        send_to_logger(message, safe_get_messages(user_id).AUDIO_HELP_SHOWN_LOG_MSG)
        return
    save_user_tags(user_id, tags)
    
    # Extract playlist parameters from the message
    full_string = text or message.caption or ""
    _, video_start_with, video_end_with, playlist_name, _, _, tag_error = extract_url_range_tags(full_string)
    # Correct video_count calculation for negative indices
    if video_start_with < 0 and video_end_with < 0:
        video_count = abs(video_end_with) - abs(video_start_with) + 1
    elif video_start_with > video_end_with:
        video_count = abs(video_start_with - video_end_with) + 1
    else:
        video_count = video_end_with - video_start_with + 1
    
    # Checking the range limit
    if not check_playlist_range_limits(url, video_start_with, video_end_with, app, message):
        return
    
    # Note: cached_video_info=None for direct calls (no optimization available)
    down_and_audio(app, message, url, tags, quality_key="mp3", playlist_name=playlist_name, video_count=video_count, video_start_with=video_start_with, format_override="ba", cached_video_info=None)
