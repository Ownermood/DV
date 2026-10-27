"""Main text pipeline: every private text message the front door (COMMANDS/simple_ui) lets through.

    /audio <url>   MP3 (sent internally by the MP3 buttons)
    /img <url>     gallery-dl (photos, carousels; internal fallback)
    <url>          TeraBox adapter -> gallery-dl-only sites -> yt-dlp, gallery-dl on failure

Users only have /start and /help (simple_ui), admins have the admin panel (admin_panel).
"""
from pyrogram import enums, filters

from CONFIG.config import Config
from CONFIG.logger_msg import LoggerMsg, get_logger_msg
from CONFIG.messages import safe_get_messages
from COMMANDS.image_cmd import image_command
from DATABASE.firebase_init import is_user_blocked
from HELPERS.app_instance import get_app
from HELPERS.decorators import reply_with_keyboard, background_handler
from HELPERS.limitter import is_user_in_channel
from HELPERS.logger import logger, send_to_user
from HELPERS.safe_messeger import safe_send_message
from URL_PARSERS.tags import extract_url_range_tags
from URL_PARSERS.video_extractor import video_url_extractor

app = get_app()


@app.on_message(filters.text & filters.private)
@reply_with_keyboard
@background_handler(label="url_distractor")
def url_distractor(app, message):
    user_id = message.chat.id
    is_admin = int(user_id) in Config.ADMIN
    text = (message.text or "").strip()

    if text.startswith(Config.AUDIO_COMMAND):
        from COMMANDS.audio_cmd import audio_command_handler
        audio_command_handler(app, message)
        return

    if text.startswith(Config.IMG_COMMAND):
        image_command(app, message)
        return

    # Non-admins must have joined the channel (if one is set)
    if not is_admin and not is_user_in_channel(app, message):
        return

    # If the message contains a URL, process without explicit commands:
    # 1) Try yt-dlp flow (video_url_extractor)
    # 2) On failure, fallback to gallery-dl (/img handler)
    # Use updated message.text if it was changed
    final_text = message.text if hasattr(message, 'text') and message.text else text
    if ("https://" in final_text) or ("http://" in final_text):
        # Only the platforms listed in COMMANDS/simple_ui.PLATFORMS are served (private and groups).
        from COMMANDS.simple_ui import detect_platform, unsupported_text
        if detect_platform(final_text) is None:
            safe_send_message(user_id, unsupported_text(user_id), parse_mode=enums.ParseMode.HTML, message=message)
            return
        if not is_user_blocked(message):
            # Check rate limit before processing URL
            from HELPERS.rate_limiter import check_rate_limit
            allowed, rate_limit_msg = check_rate_limit(user_id, is_admin)
            if not allowed:
                messages = safe_get_messages(user_id)
                safe_send_message(
                    user_id,
                    rate_limit_msg or messages.RATE_LIMIT_EXCEEDED_MSG if hasattr(messages, 'RATE_LIMIT_EXCEEDED_MSG') else "Rate limit exceeded. Please wait.",
                    message=message
                )
                return
            
            from COMMANDS.subtitles_cmd import clear_subs_check_cache
            clear_subs_check_cache()
            # Optional isolated TeraBox adapter. It is disabled by default and
            # only receives URLs whose parsed hostname matches the allowlist.
            try:
                from ADAPTERS.terabox import handle_terabox
                if handle_terabox(app, message):
                    return
            except Exception as terabox_error:
                logger.exception(f"TeraBox adapter error: {terabox_error}")
                from ADAPTERS.terabox import is_terabox_url
                from COMMANDS.simple_ui import extract_url
                if is_terabox_url(extract_url(message.text or "") or ""):  # yt-dlp / gallery-dl can't open TeraBox
                    send_to_user(message, "📦 TeraBox download failed. Please try again in a minute.")
                    return

            # Centralized router to gallery-dl for certain links
            try:
                try:
                    from .engine_router import route_if_gallerydl_only  # type: ignore
                except Exception:
                    from URL_PARSERS.engine_router import route_if_gallerydl_only  # type: ignore
                if route_if_gallerydl_only(app, message):
                    return
            except Exception as route_e:
                logger.error(LoggerMsg.URL_EXTRACTOR_ENGINE_ROUTER_ERROR_LOG_MSG.format(error=route_e))
            try:
                logger.info(f"🔍 [DEBUG] url_extractor: before calling video_url_extractor, message.text='{message.text}'")
                video_url_extractor(app, message)
            except Exception as e:
                logger.error(LoggerMsg.URL_EXTRACTOR_VIDEO_EXTRACTOR_FAILED_LOG_MSG.format(e=e))
                try:
                    # Create proper /img command from URL
                    from HELPERS.safe_messeger import fake_message
                    
                    # Extract URL and range from original message
                    url, video_start_with, video_end_with, playlist_name, tags, tags_text, tag_error = extract_url_range_tags(message.text)
                    
                    if url:
                        # Create fallback command with range if available
                        if video_start_with and video_end_with and (video_start_with != 1 or video_end_with != 1):
                            fallback_text = f"/img {video_start_with}-{video_end_with} {url}"
                        else:
                            fallback_text = f"/img {url}"
                        
                        # Add tags if available
                        if tags_text:
                            fallback_text += f" {tags_text}"
                        
                        # Create fake message for gallery-dl command
                        # For groups, preserve original chat_id and message_thread_id
                        original_chat_id = message.chat.id if hasattr(message, 'chat') else message.chat.id
                        message_thread_id = getattr(message, 'message_thread_id', None) if hasattr(message, 'message_thread_id') else None
                        fake_msg = fake_message(fallback_text, message.chat.id, original_chat_id=original_chat_id, message_thread_id=message_thread_id, original_message=message)
                        
                        # Execute gallery-dl command
                        image_command(app, fake_msg)
                        logger.info(get_logger_msg().URL_EXTRACTOR_GALLERY_DL_FALLBACK_LOG_MSG.format(fallback_text=fallback_text))
                    else:
                        logger.error("No URL found for gallery-dl fallback")
                        
                except Exception as e2:
                    logger.error(LoggerMsg.URL_EXTRACTOR_GALLERY_DL_FALLBACK_FAILED_LOG_MSG.format(e2=e2))
        return

    logger.info(LoggerMsg.URL_EXTRACTOR_NO_MATCHING_COMMAND_LOG_MSG.format(user_id=user_id))
