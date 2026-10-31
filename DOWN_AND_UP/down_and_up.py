import shutil as _shutil_mod  # module-level alias: functions below import shutil locally
# Version: 1.0.1
# ########################################
# Download_and_up function
# ########################################

import itertools
import os
import threading
import time
import subprocess
import traceback
import yt_dlp
import re
from HELPERS.app_instance import get_app
from HELPERS.logger import logger, send_to_logger, send_to_user, send_error_to_user, get_log_channel, log_error_to_channel
from CONFIG.logger_msg import LoggerMsg
from CONFIG.messages import safe_get_messages
from HELPERS.limitter import TimeFormatter, humanbytes, check_file_size_limit
from HELPERS.download_status import set_active_download, clear_download_start_time, check_download_timeout, start_hourglass_animation, start_cycle_progress, playlist_errors_lock, playlist_errors
from HELPERS.progress_ui import bar_blocks, progress_card, edit_card
from HELPERS.safe_messeger import safe_delete_messages, safe_edit_message_text, safe_forward_messages
from HELPERS.filesystem_hlp import sanitize_filename, sanitize_filename_strict, cleanup_user_temp_files, create_directory, check_disk_space
from DOWN_AND_UP.ffmpeg import get_duration_thumb, get_video_info_ffprobe, create_default_thumbnail, split_video_2, ffmpeg_extract_subclip
from DOWN_AND_UP.sender import send_videos
from DATABASE.firebase_init import write_logs
from URL_PARSERS.tags import generate_final_tags, save_user_tags
from URL_PARSERS.youtube import download_thumbnail, extract_youtube_id
from URL_PARSERS.nocookie import is_no_cookie_domain
from URL_PARSERS.filter_check import is_no_filter_domain
from URL_PARSERS.filter_utils import create_smart_match_filter
from URL_PARSERS.thumbnail_downloader import download_thumbnail as download_universal_thumbnail
from HELPERS.pot_helper import add_pot_to_ytdl_opts
from CONFIG.config import Config
from CONFIG.limits import LimitsConfig
from URL_PARSERS.playlist_utils import is_playlist_with_range
from URL_PARSERS.normalizer import get_clean_playlist_url
from DATABASE.cache_db import get_cached_playlist_videos, save_to_video_cache
from HELPERS.qualifier import get_quality_by_min_side
from HELPERS.safe_messeger import safe_forward_messages  # Duplicate import to avoid visibility issues
from pyrogram import enums
from pyrogram.types import ReplyParameters
from URL_PARSERS.tags import extract_url_range_tags
from HELPERS.fallback_helper import should_fallback_to_gallery_dl

# Get app instance for decorators
app = get_app()


def _handle_quality_key_error(e: Exception, split_msg_ids: list, is_playlist: bool, successful_uploads: int, indices_to_download: list, video_count: int, user_id: int, proc_msg_id: int, message, app, url: str = None, safe_quality_key: str = None):
    messages = safe_get_messages(user_id)
    """Universal handler for quality_key errors that ensures final actions are completed"""
    logger.info(f"quality_key error ignored (non-critical): {e}")
    logger.info(f"Continuing after quality_key error - split_msg_ids={split_msg_ids}, is_playlist={is_playlist}")
    
    # Check if all downloads completed successfully
    # For split videos, check if we have split_msg_ids; for regular videos, check successful_uploads
    logger.info(f"Final check after quality_key error: successful_uploads={successful_uploads}, len(indices_to_download)={len(indices_to_download)}, split_msg_ids={split_msg_ids}, is_playlist={is_playlist}")

def _own_thumb(chat_id, url) -> bool:
    """The person who asked (group member or private user) has an own thumbnail for this platform."""
    try:
        from HELPERS import cancel
        from COMMANDS.user_prefs import has_thumb_for_url
        return has_thumb_for_url(cancel.owner(chat_id), url)
    except Exception:
        return False


def _save_video_cache_with_logging(url: str, safe_quality_key: str, message_ids: list, original_text: str = None, user_id: int = None):
    """Save video to cache with channel type logging."""
    try:
        # Check if user has send_as_file enabled
        if user_id is not None:
            user_args = {}  # per-user yt-dlp args were removed
            send_as_file = user_args.get("send_as_file", False)
            if send_as_file:
                logger.info(LoggerMsg.DOWN_UP_SKIPPING_CACHE_SEND_AS_FILE_LOG_MSG.format(user_id=user_id, url=url, quality=safe_quality_key))
                return
        
        if user_id is not None and _own_thumb(user_id, url):
            return  # the person's own thumbnail is on this copy: not for other users
        # Determine channel type for logging
        from HELPERS.porn import is_porn
        is_nsfw = is_porn(url, "", "", None)
        logger.info(LoggerMsg.DOWN_UP_IS_PORN_CHECK_LOG_MSG.format(url=url, is_nsfw=is_nsfw))
        channel_type = "NSFW" if is_nsfw else "regular"
        
        # Don't cache NSFW content
        if is_nsfw:
            logger.info(LoggerMsg.DOWN_UP_SKIPPING_CACHE_NSFW_LOG_MSG.format(url=url, quality=safe_quality_key, channel_type=channel_type))
            return
        
        logger.info(LoggerMsg.DOWN_UP_ABOUT_TO_SAVE_VIDEO_LOG_MSG.format(url=url, quality=safe_quality_key, message_ids=message_ids, channel_type=channel_type))
        save_to_video_cache(url, safe_quality_key, message_ids, original_text=original_text)
        logger.info(LoggerMsg.DOWN_UP_SAVE_REQUESTED_LOG_MSG.format(quality=safe_quality_key, channel_type=channel_type))
    except Exception as e:
        logger.error(LoggerMsg.DOWN_UP_SAVE_FAILED_LOG_MSG.format(quality=safe_quality_key, error=e))



#@reply_with_keyboard
def down_and_up(app, message, url, playlist_name, video_count, video_start_with, tags_text, force_no_title=False, format_override=None, quality_key=None, cookies_already_checked=False, use_proxy=False, cached_video_info=None, clear_subs_cache_on_start=True):
    # Reset the checked cookie-source cache for a new download task
    user_id = message.chat.id
    from COMMANDS.cookies_cmd import reset_checked_cookie_sources
    reset_checked_cookie_sources(user_id)
    logger.info(f"🔄 [DEBUG] Reset checked cookie sources for new download task for user {user_id}")
    messages = safe_get_messages(message.chat.id)
    """
    Now if part of the playlist range is already cached, we first repost the cached indexes, then download and cache the missing ones, without finishing after reposting part of the range.
    """
    # Import required modules at the beginning
    from URL_PARSERS.youtube import is_youtube_url
    from COMMANDS.cookies_cmd import is_youtube_geo_error, retry_download_with_different_cookies, retry_download_with_proxy
    
    playlist_indices = []
    playlist_msg_ids = []
    playlist_video_urls = {}  # A map of unique video URLs per index: {index: video_url}
    found_type = None
    already_forwarded_to_log = False  # Initialize variable to track log forwarding status
    safe_quality_key = quality_key if quality_key is not None else "best"  # Initialize safe_quality_key
    split_msg_ids = []  # Initialize split_msg_ids for split videos
    caption_lst = []  # Initialize caption_lst for split videos
    last_video_msg_id = None  # Initialize last_video_msg_id for caching
    user_id = message.chat.id
    # Ensure fresh subtitle state at the start of a task even for direct calls (bypassing Always Ask)
    successful_uploads = 0  # Initialize successful_uploads counter
    indices_to_download = []  # Initialize indices_to_download list
    proc_msg_id = None  # Initialize proc_msg_id
    logger.info(f"down_and_up called: url={url}, quality_key={quality_key}, format_override={format_override}, video_count={video_count}, video_start_with={video_start_with}")
    
    # STRICT: keep the original text with range tags for fallback parsing
    original_message_text = message.text or message.caption or ""
    logger.info(f"[ORIGINAL TEXT] Saved for fallback: {original_message_text}")
    
    # Initialize retry guards early to avoid UnboundLocalError
    did_proxy_retry = False
    did_cookie_retry = False
    is_hls = False
    error_message_sent = False  # Flag to prevent duplicate error messages
    
    # Determine forced NSFW via user tags
    try:
        _u, _s, _e, _p, _tags, _tags_text, _err = extract_url_range_tags(original_message_text)
        user_forced_nsfw = any(t.lower() in ("#nsfw", "#porn") for t in (_tags or []))
    except Exception:
        user_forced_nsfw = False
    
    # Check if format contains /bestaudio (audio-only format)
    if format_override and '/bestaudio' in format_override:
        logger.info(f"Audio-only format detected in down_and_up: {format_override}, redirecting to down_and_audio")
        from DOWN_AND_UP.down_and_audio import down_and_audio
        # Pass cached video info to down_and_audio for optimization
        down_and_audio(app, message, url, tags_text, quality_key=quality_key, format_override=format_override, cookies_already_checked=cookies_already_checked, cached_video_info=cached_video_info)
        return
    
    # Check if LINK mode is enabled - if yes, get direct link instead of downloading
        # Continue with normal download if LINK mode check fails
    # We define a playlist not only by the number of videos, but also by the presence of a range in the URL
    original_text = message.text or message.caption or ""
    is_playlist = video_count > 1 or is_playlist_with_range(original_text)
    
    # Extract video_end_with from original_text (if present)
    _, parsed_start, parsed_end, _, _, _, _ = extract_url_range_tags(original_text)
    video_end_with = parsed_end if parsed_end != 1 or parsed_start != 1 else (video_start_with + video_count - 1)
    
    # Determine whether reverse order is needed (when start > end)
    # For negative indices: -1..-7 implies reverse order (7, 6, 5, 4, 3, 2, 1)
    is_reverse_order = False
    has_negative_indices = False
    use_range_download = False  # Declare upfront
    if is_playlist and video_start_with is not None and video_end_with is not None:
        # If both are negative, always use reverse order
        if video_start_with < 0 and video_end_with < 0:
            is_reverse_order = True
            has_negative_indices = True
        # If start > end, it's reverse order
        elif video_start_with > video_end_with:
            is_reverse_order = True
    
    # Build the list of indices, taking reverse order into account
    # For negative indices, we'll convert them to positive after we know total video count
    if is_playlist:
        if has_negative_indices:
            # For negative indices we first build a list with negative values,
            # then convert them to positive after we know total video count.
            # -1..-7 means: download in order 7, 6, 5, 4, 3, 2, 1 (last to first).
            # Example list for -1..-7: [-1, -2, -3, -4, -5, -6, -7]
            if abs(video_start_with) < abs(video_end_with):
                # -1..-7: build [-1, -2, -3, -4, -5, -6, -7]
                requested_indices = list(range(video_start_with, video_end_with - 1, -1))
            else:
                # -7..-1: build [-7, -6, -5, -4, -3, -2, -1]
                requested_indices = list(range(video_start_with, video_end_with + 1, 1))
        elif is_reverse_order:
            # Reverse order: from start to end inclusive, reversed
            requested_indices = list(range(video_start_with, video_end_with - 1, -1))
        else:
            # Forward order: from start to end inclusive
            requested_indices = list(range(video_start_with, video_start_with + video_count))
    else:
        requested_indices = []
    playlist_indices_all = requested_indices[:] if requested_indices else []
    cached_videos = {}
    uncached_indices = []
    if safe_quality_key and is_playlist and not _own_thumb(user_id, url):
        # Check if Always Ask mode is enabled - if yes, skip cache completely
        from HELPERS.porn import is_porn
        is_nsfw = is_porn(url, "", "", None) or user_forced_nsfw
        logger.info(f"[FALLBACK] is_porn check for {url}: {is_porn(url, '', '', None)}, user_forced_nsfw: {user_forced_nsfw}, final is_nsfw: {is_nsfw}")

        if not is_nsfw:
            cached_videos = get_cached_playlist_videos(get_clean_playlist_url(url), safe_quality_key, requested_indices)
            logger.info(f"[VIDEO CACHE] Checking cache for regular playlist: url={url}, quality={safe_quality_key}")
        else:
            logger.info(f"[VIDEO CACHE] Skipping cache check for NSFW playlist: url={url}, quality={safe_quality_key}")
        
        uncached_indices = [i for i in requested_indices if i not in cached_videos]
        # First, repost the cached ones (skip if send_as_file is enabled)
        if cached_videos and (not use_range_download or len(uncached_indices) == 0):
            # Check if send_as_file is enabled - if so, skip cache repost
            user_args = {}  # per-user yt-dlp args were removed
            send_as_file = user_args.get("send_as_file", False)
            
            if not send_as_file:
                for index in requested_indices:
                    if index in cached_videos:
                        try:
                            # For cached content, always use regular channel (no NSFW/PAID in cache)
                            from_chat_id = get_log_channel("video")
                            channel_type = "regular"
                            
                            logger.info(f"[VIDEO CACHE] Reposting video {index} from channel {from_chat_id} to user {user_id}, message_id={cached_videos[index]}")
                            forward_kwargs = {
                                'chat_id': user_id,
                                'from_chat_id': from_chat_id,
                                'message_ids': [cached_videos[index]]
                            }
                            # Only apply thread_id in groups/channels, not in private chats
                            if getattr(message.chat, "type", None) != enums.ChatType.PRIVATE:
                                thread_id = getattr(message, 'message_thread_id', None)
                                if thread_id:
                                    forward_kwargs['message_thread_id'] = thread_id
                            app.forward_messages(**forward_kwargs)
                        except Exception as e:
                            logger.error(f"down_and_up: error reposting cached video index={index}: {e}")
            else:
                # If send_as_file is enabled, treat all indices as uncached
                logger.info(f"[VIDEO CACHE] send_as_file enabled for user {user_id}, skipping cache repost for playlist")
                uncached_indices = requested_indices
            
            if len(uncached_indices) == 0:
                app.send_message(user_id, safe_get_messages(user_id).PLAYLIST_SENT_FROM_CACHE_MSG.format(cached=len(cached_videos), total=len(requested_indices)), reply_parameters=ReplyParameters(message_id=message.id))
                send_to_logger(message, LoggerMsg.PLAYLIST_VIDEOS_SENT_FROM_CACHE.format(quality=safe_quality_key, user_id=user_id))
                return
            else:
                app.send_message(user_id, safe_get_messages(user_id).CACHE_PARTIAL_MSG.format(cached=len(cached_videos), total=len(requested_indices)), reply_parameters=ReplyParameters(message_id=message.id))
        elif cached_videos:
            logger.info("[VIDEO CACHE] Skipping partial cache replay for negative range to avoid duplicate downloads")
            uncached_indices = requested_indices
        else:
            logger.info(f"[VIDEO CACHE] Skipping cache check for playlist because Always Ask mode is enabled: url={url}, quality={safe_quality_key}")
            # Set all indices as uncached when Always Ask mode is enabled
            uncached_indices = requested_indices

    status_msg = None
    status_msg_id = None
    hourglass_msg = None
    hourglass_msg_id = None
    anim_thread = None
    stop_anim = threading.Event()
    download_started_msg_id = None
    proc_msg = None
    proc_msg_id = None
    try:
        user_dir = os.path.join("users", str(user_id))

        # One progress card per download (edited in place, deleted after upload)
        proc_msg = app.send_message(user_id, progress_card("search", 0), parse_mode=enums.ParseMode.HTML, reply_parameters=ReplyParameters(message_id=message.id))
        proc_msg_id = proc_msg.id
        error_message = ""
        status_msg = None
        status_msg_id = None
        hourglass_msg = None
        hourglass_msg_id = None
        anim_thread = start_hourglass_animation(user_id, hourglass_msg_id, stop_anim)

        # Check if there's enough disk space (estimate 2GB per video)
        user_dir_name = os.path.abspath(os.path.join("users", str(user_id)))
        create_directory(user_dir_name)

        # Estimate required space: first use yt-dlp exact/approx size,
        # then estimate via bitrate and duration; as a last resort assume 2 GB.
        required_bytes = 2 * 1024 * 1024 * 1024
        try:
            # Try to use cached info first for size check
            if cached_video_info:
                info_probe = cached_video_info
                logger.info(f"✅ [OPTIMIZATION] Using cached video info for size check")
            else:
                from DOWN_AND_UP.yt_dlp_hook import get_video_formats
                info_probe = get_video_formats(url, user_id, cookies_already_checked=cookies_already_checked)
                logger.info(f"⚠️ [OPTIMIZATION] Had to fetch video info for size check")
            size = 0
            if isinstance(info_probe, dict):
                size = info_probe.get('filesize') or info_probe.get('filesize_approx') or 0
                if not size:
                    # fallback via tbr*duration
                    tbr = info_probe.get('tbr') or 0  # total bitrate in Kbps
                    duration = info_probe.get('duration') or 0
                    if tbr and duration:
                        # tbr Kbps -> bytes/sec: tbr*1000/8, *duration
                        size = int((float(tbr) * 1000.0 / 8.0) * float(duration))
                    else:
                        # last resort: take the maximum tbr across formats
                        formats = info_probe.get('formats') or []
                        best_tbr = 0
                        for f in formats:
                            ftbr = f.get('tbr') or 0
                            if ftbr and ftbr > best_tbr:
                                best_tbr = ftbr
                        if best_tbr and duration:
                            size = int((float(best_tbr) * 1000.0 / 8.0) * float(duration))
            if size and size > 0:
                required_bytes = int(size * 1.2)  # 20% buffer
        except Exception:
            pass

        if not check_disk_space(user_dir_name, required_bytes):
            send_to_user(message, safe_get_messages(user_id).ERROR_NO_DISK_SPACE_MSG)
            return

        # Create user directory (subscription already checked in video_extractor)
        user_dir = os.path.join("users", str(user_id))
        if not os.path.exists(user_dir):
            os.makedirs(user_dir, exist_ok=True)

        # Try to get download directory from ask_quality_menu first
        from DOWN_AND_UP.dl_state import get_user_download_dir, generate_download_dir_name
        user_dir_name = get_user_download_dir(user_id)
        
        # If no download directory from ask_quality_menu, create one
        if not user_dir_name or not os.path.exists(user_dir_name):
            try:
                # Generate download directory name based on URL
                dir_name = generate_download_dir_name(url)
                unique_download_dir = os.path.join(user_dir, "downloads", dir_name)
                os.makedirs(unique_download_dir, exist_ok=True)
                
                # Update user_dir_name to use the unique directory
                user_dir_name = unique_download_dir
                
                logger.info(f"Created download directory: {unique_download_dir}")
            except Exception as e:
                logger.warning(f"Failed to create download directory, using default: {e}")
                # Fallback to original behavior
                user_dir_name = os.path.abspath(os.path.join("users", str(user_id)))


        # Pre-cleanup: remove all media files from unique download directory before starting
        try:
            logger.info(f"Pre-cleanup: removing old media files from unique directory {user_dir_name}")
            if os.path.exists(user_dir_name):
                for root, dirs, files in os.walk(user_dir_name):
                    for file in files:
                        file_path = os.path.join(root, file)
                        try:
                            # Remove all media files (keep .txt and .json files)
                            if file.lower().endswith((
                                '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tiff',
                                '.mp4', '.m4v', '.avi', '.mov', '.mkv', '.webm', '.flv',
                                '.mp3', '.wav', '.ogg', '.m4a',
                                '.pdf', '.doc', '.docx', '.zip', '.rar', '.7z'
                            )):
                                os.remove(file_path)
                                logger.info(f"Pre-cleanup: removed file {file_path}")
                        except Exception as e:
                            logger.warning(f"Pre-cleanup: failed to remove file {file_path}: {e}")
                    
                    # Remove empty directories (except unique download root)
                    for dir_name in dirs:
                        dir_path = os.path.join(root, dir_name)
                        try:
                            if os.path.exists(dir_path) and not os.listdir(dir_path) and dir_path != user_dir_name:
                                os.rmdir(dir_path)
                                logger.info(f"Pre-cleanup: removed empty directory {dir_path}")
                        except Exception as e:
                            logger.warning(f"Pre-cleanup: failed to remove directory {dir_path}: {e}")
            
            # Create protection file for parallel downloads
            from HELPERS.filesystem_hlp import is_parallel_download_allowed, create_protection_file
            if is_parallel_download_allowed(message):
                create_protection_file(user_dir_name)
            
            logger.info(f"Pre-cleanup completed for unique directory {user_dir_name}")
        except Exception as e:
            logger.warning(f"Pre-cleanup failed for unique directory {user_dir_name}: {e}")

        # Reset of the flag of errors for the new launch of the playlist
        if playlist_name:
            with playlist_errors_lock:
                error_key = f"{user_id}_{playlist_name}"
                if error_key in playlist_errors:
                    del playlist_errors[error_key]

        # if use_default_format is True, then do not take from format.txt, but use default ones
        custom_format_path = os.path.join(user_dir_name, "format.txt")
        use_default_format = False
        if not format_override and os.path.exists(custom_format_path):
            with open(custom_format_path, "r", encoding="utf-8") as f:
                custom_format = f.read().strip()
            if custom_format == "ALWAYS_ASK":
                use_default_format = True
        if use_default_format:
            format_override = None

        # Get user's merge_output_format preference once
        user_args = {}  # per-user yt-dlp args were removed
        user_merge_format = user_args.get('merge_output_format', 'mp4')
        
        if format_override:
            attempts = [{'format': format_override, 'prefer_ffmpeg': True, 'merge_output_format': user_merge_format}]
        else:
            # if use_default_format is True, then do not take from format.txt, but use default ones
            if use_default_format:
                attempts = [
                    {'format': 'bv*[vcodec*=avc1][height<=1080][height>720]+ba[acodec*=mp4a]/bv*[vcodec*=avc1][height<=1080]+ba[acodec*=mp4a]/bv*[vcodec*=avc1]+ba/bv+ba/best', 'prefer_ffmpeg': True, 'merge_output_format': user_merge_format, 'extract_flat': False},
                    {'format': 'best', 'prefer_ffmpeg': False, 'extract_flat': False}
                ]
            else:
                if os.path.exists(custom_format_path):
                    with open(custom_format_path, "r", encoding="utf-8") as f:
                        custom_format = f.read().strip()
                    if custom_format.lower() == "best":
                        attempts = [{'format': custom_format, 'prefer_ffmpeg': False}]
                    else:
                        attempts = [{'format': custom_format, 'prefer_ffmpeg': True, 'merge_output_format': user_merge_format}]
                else:
                    attempts = [
                        {'format': 'bv*[vcodec*=avc1][height<=1080][height>720]+ba[acodec*=mp4a]/bv*[vcodec*=avc1][height<=1080]+ba[acodec*=mp4a]/bv*[vcodec*=avc1]+ba/bv+ba/best',
                        'prefer_ffmpeg': True, 'merge_output_format': user_merge_format, 'extract_flat': False},
                        {'format': 'bv*[vcodec*=avc1]+ba[acodec*=mp4a]/bv*[vcodec*=avc1]+ba/bestvideo+bestaudio/best/bv+ba/best',
                        'prefer_ffmpeg': True, 'merge_output_format': user_merge_format, 'extract_flat': False},
                        {'format': 'best', 'prefer_ffmpeg': False, 'extract_flat': False}
                    ]

        # Single status message: the progress card in proc_msg is the only message shown.
        status_msg_id = None
        hourglass_msg_id = None

        # Get info_dict to estimate the size of the selected quality
        try:
            ydl_opts = {
                'quiet': True,
                'extractor_args': {
                    'youtubetab': {'skip': ['authcheck']}
                }
            }
            # Try to use cookies from download directory first, then fallback to user root
            download_cookie_path = os.path.join(user_dir_name, "cookie.txt")
            user_cookie_path = os.path.join("users", str(user_id), "cookie.txt")
            
            if is_youtube_url(url):
                from COMMANDS import cookie_store
                if cookie_store.first_active("youtube"):
                    ydl_opts['cookiefile'] = cookie_store.first_active("youtube")
            elif os.path.exists(download_cookie_path):
                ydl_opts['cookiefile'] = download_cookie_path
                logger.info(f"Using cookies from download directory: {download_cookie_path}")
            elif os.path.exists(user_cookie_path):
                ydl_opts['cookiefile'] = user_cookie_path
                logger.info(f"Using cookies from user directory: {user_cookie_path}")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                pre_info = ydl.extract_info(url, download=False)
            # Normalize to dict and check None
            if isinstance(pre_info, list):
                pre_info = (pre_info[0] if len(pre_info) > 0 else {})
            elif isinstance(pre_info, dict) and 'entries' in pre_info and isinstance(pre_info['entries'], list) and pre_info['entries']:
                pre_info = pre_info['entries'][0]
            if pre_info is None:
                logger.warning("pre_info is None, skipping size check")
                pre_info = {}
        except Exception as e:
            logger.warning(f"Failed to extract info for size check: {e}")
            pre_info = {}

        # Find format for selected safe_quality_key
        selected_format = None
        for f in pre_info.get('formats', []):
            w = f.get('width')
            h = f.get('height')
            if w and h:
                qk = get_quality_by_min_side(w, h)
                if str(qk) == str(safe_quality_key):
                    selected_format = f
                    break

        # If you did not find the format, STOP downloading!
        #if not selected_format:
            #logger.warning(f"[SIZE CHECK] Could not determine format for quality_key={quality_key}. Download will not start.")
            #app.send_message(
                #user_id,
                #"Unable to determine the file size for the selected quality. Please try another quality or check your cookies.",
                #reply_to_message_id=message.id
            #)
            #return

        
        # Checking the limit
        #from _config import Config
        BYTES_IN_GIB = 1024 ** 3
        max_size_gb = getattr(Config, 'MAX_FILE_SIZE', 10)
        max_size_bytes = int(max_size_gb * BYTES_IN_GIB)
        # Get the file size
        if selected_format is None:
            logger.warning("selected_format is None, skipping size check")
            filesize = 0
            allowed = True  # Allow download if we can't determine the size
        else:
            filesize = selected_format.get('filesize') or selected_format.get('filesize_approx')
            if filesize is None:
                # fallback on rating
                tbr = selected_format.get('tbr')
                duration = selected_format.get('duration')
                if tbr is not None and duration is not None:
                    try:
                        filesize = float(tbr) * float(duration) * 125
                    except (TypeError, ValueError):
                        filesize = None
                else:
                    filesize = None
                
                if filesize is None:
                    width = selected_format.get('width')
                    height = selected_format.get('height')
                    duration = selected_format.get('duration')
                    if width is not None and height is not None and duration is not None:
                        try:
                            filesize = int(width) * int(height) * float(duration) * 0.07
                        except (TypeError, ValueError):
                            filesize = 0
                    else:
                        filesize = 0

            allowed = check_file_size_limit(selected_format, max_size_bytes=max_size_bytes, message=message)
        
        # Secure file size logging
        try:
            filesize_val = float(filesize) if filesize is not None else 0
            if filesize_val > 0:
                size_gb = filesize_val/(1024**3)
                logger.info(f"[SIZE CHECK] safe_quality_key={safe_quality_key}, determined size={size_gb:.2f} GB, limit={max_size_gb} GB, allowed={allowed}")
            else:
                logger.info(f"[SIZE CHECK] safe_quality_key={safe_quality_key}, size unknown, limit={max_size_gb} GB, allowed={allowed}")
        except (TypeError, ValueError):
            logger.info(f"[SIZE CHECK] safe_quality_key={safe_quality_key}, size unknown, limit={max_size_gb} GB, allowed={allowed}")

        if not allowed:
            app.send_message(
                user_id,
                safe_get_messages(user_id).ERROR_FILE_SIZE_LIMIT_MSG.format(limit=max_size_gb),
                reply_parameters=ReplyParameters(message_id=message.id)
            )
            log_error_to_channel(message, safe_get_messages(user_id).SIZE_LIMIT_EXCEEDED.format(max_size_gb=max_size_gb), url)
            logger.warning(f"[SIZE CHECK] Download for safe_quality_key={safe_quality_key} was blocked due to size limit.")
            return
        else:
            logger.info(f"[SIZE CHECK] Download for safe_quality_key={safe_quality_key} is allowed and will proceed.")

        current_total_process = ""
        last_update = 0
        full_bar = bar_blocks(10)
        first_progress_update = True  # Flag for tracking the first update
        progress_start_time = time.time()
        # One-time retry guards to avoid infinite retry loops across attempts
        # (already initialized at the beginning of the function)
        
        # Check if this is an HLS stream (needed for progress_func)
        # This will be updated later based on actual format detection
        is_hls = ("m3u8" in url.lower())

        def progress_func(d):
            messages = safe_get_messages(message.chat.id)
            nonlocal last_update, first_progress_update, is_hls
            from HELPERS.cancel import check as _cancel_check
            _cancel_check(user_id)  # ❌ Cancel tapped: abort yt-dlp right here
            # Check the timeout
            if check_download_timeout(user_id):
                raise Exception(f"Download timeout exceeded ({safe_get_messages(user_id).DOWNLOAD_TIMEOUT // 3600} hours)")
            current_time = time.time()
            
            
            # Calculate elapsed time and minutes passed
            elapsed = max(0, current_time - progress_start_time)
            minutes_passed = int(elapsed // 60)
            
            # Adaptive throttle: linear slow-down; after 1h fixed 90s
            if minutes_passed and minutes_passed >= 60:
                interval = 90.0
            else:
                # 0-4 min: 3s, 5-9: 4s, ..., 55-59: 14s
                interval = 3.0 + max(0, minutes_passed // 5)
            
            if current_time - last_update < interval:
                return
                
            if d.get("status") == "downloading":
                downloaded = d.get("downloaded_bytes", 0)
                # yt-dlp may provide only total_bytes_estimate for some sites
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                percent = (downloaded / total * 100) if total else 0
                blocks = int(percent // 10)
                bar = bar_blocks(blocks)
                
                # Update progress in stats
                
                # For HLS, update progress data for cycle animation
                if is_hls and hasattr(progress_func, 'progress_data') and progress_func.progress_data:
                    progress_func.progress_data['downloaded_bytes'] = downloaded
                    progress_func.progress_data['total_bytes'] = total
                try:
                    # With the first renewal of progress, we delete the first posts Processing
                    if first_progress_update:
                        try:
                            # Bots can't use get_chat_history here, so we only log
                            logger.info("Skipping message cleanup - bots cannot use get_chat_history")
                        except Exception as e:
                            logger.error(f"Error in message cleanup: {e}")
                            # Check if error is related to quality_key
                            if "'quality_key'" in str(e):
                                _handle_quality_key_error(e, split_msg_ids, is_playlist, successful_uploads, indices_to_download, video_count, user_id, proc_msg_id, message, app)
                        first_progress_update = False

                    progress_text = progress_card("download", percent, current_total_process)
                    logger.info(f"Updating progress for user {user_id}, message {proc_msg_id}: {progress_text}")
                    result = edit_card(safe_edit_message_text, user_id, proc_msg_id, progress_text)
                    if result is None:
                        logger.warning(f"Failed to update progress message {proc_msg_id} for user {user_id} - message may have been deleted")
                except Exception as e:
                    logger.error(f"Error updating progress: {e}")
                    # Check if error is related to quality_key
                    if "'quality_key'" in str(e):
                        _handle_quality_key_error(e, split_msg_ids, is_playlist, successful_uploads, indices_to_download, video_count, user_id, proc_msg_id, message, app)
            elif d.get("status") == "finished":
                # Update progress to 100% on completion
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                try:
                    safe_edit_message_text(user_id, proc_msg_id, safe_get_messages(user_id).VIDEO_DOWNLOAD_COMPLETE_MSG.format(process=current_total_process, bar=full_bar))
                except Exception as e:
                    logger.error(f"Error updating progress: {e}")
                    # Check if error is related to quality_key
                    if "'quality_key'" in str(e):
                        _handle_quality_key_error(e, split_msg_ids, is_playlist, successful_uploads, indices_to_download, video_count, user_id, proc_msg_id, message, app)
            elif d.get("status") == "error":
                # Reset progress on error
                downloaded = d.get("downloaded_bytes", 0)
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                logger.error("Error occurred during download.")
                send_error_to_user(message, safe_get_messages(user_id).DOWNLOAD_ERROR_GENERIC)
            last_update = current_time

        successful_uploads = 0

        def try_download(url, attempt_opts):
            messages = safe_get_messages(message.chat.id)
            nonlocal current_total_process, error_message, did_cookie_retry, did_proxy_retry, is_hls, error_message_sent, is_reverse_order, use_range_download, current_playlist_items_override, range_entries_metadata
            
            # Use original filename for first attempt
            original_outtmpl = os.path.join(user_dir_name, "%(title)s.%(ext)s")
            
            # First try with original filename
            # For negative indices, use the whole range at once
            if current_playlist_items_override:
                playlist_items_str = current_playlist_items_override
            elif is_reverse_order and is_playlist:
                # For reverse order, use START:STOP:-1
                playlist_items_str = f"{current_index}:{current_index}:-1"
            else:
                # For normal cases (including negative indices already converted to positive)
                playlist_items_str = str(current_index)
            
            common_opts = {
                'playlist_items': playlist_items_str,
                'outtmpl': original_outtmpl,
                'postprocessors': [
                    {'key': 'EmbedThumbnail'},
                    {'key': 'FFmpegMetadata'}
                ],
                # Allow Unicode characters in filenames
                'restrictfilenames': False,
                'extractor_args': {
                    'generic': {'impersonate': ['chrome']},
                    'youtubetab': {'skip': ['authcheck']}
                },
                'referer': url,
                'geo_bypass': True,
                'check_certificate': False,
                'live_from_start': True,
                # Max speed: parallel fragments + 10MB chunks (YouTube throttles unchunked https)
                'concurrent_fragment_downloads': 16,
                'http_chunk_size': 10485760,
                # aria2c: 16 connections per file; not for YouTube (googlevideo throttles / refuses many parallel ranges)
                **({'external_downloader': {'http': 'aria2c'},
                   'external_downloader_args': {'aria2c': ['-x', '16', '-s', '16', '-k', '1M', '--file-allocation=none']}}
                   if _shutil_mod.which('aria2c') and not is_youtube_url(url) else {}),
                'retries': 10,
                'fragment_retries': 10,
                #'socket_timeout': 60,  # Increase socket timeout
                #'retries': 15,  # Increase retries
                #'fragment_retries': 15,  # Increase fragment retries
                #'http_chunk_size': 5242880,  # 5MB chunks for better stability
                #'buffersize': 2048,  # Increase buffer size
                #'sleep_interval': 2,  # Sleep between requests
                #'max_sleep_interval': 10,  # Max sleep between requests
                #'read_timeout': 60,  # Read timeout
                #'connect_timeout': 30  # Connect timeout
            }
            
            # Define sanitize_title_for_filename function
            def sanitize_title_for_filename(title):
                messages = safe_get_messages(message.chat.id)
                """Sanitize title for filename using strict sanitization"""
                if not title:
                    return "video"
                from HELPERS.filesystem_hlp import sanitize_filename_strict
                return sanitize_filename_strict(title)
            
            # Add match_filter only if domain is not in NO_FILTER_DOMAINS
            # Add match_filter for domain filtering and title sanitization
            def sanitize_and_filter(info):
                messages = safe_get_messages(message.chat.id)
                # First save original title for caption before sanitizing
                if 'title' in info and info['title']:
                    original_title = info['title']
                    # Save original title for caption
                    info['original_title'] = original_title
                    # Sanitize title for filename
                    sanitized_title = sanitize_title_for_filename(original_title)
                    info['title'] = sanitized_title
                    logger.info(f"Sanitized title: '{original_title}' -> '{sanitized_title}'")
                
                # Then apply domain-specific filtering
                if not is_no_filter_domain(url):
                    return create_smart_match_filter()(info)
                else:
                    logger.info(f"Skipping domain filter for domain in NO_FILTER_DOMAINS: {url}")
                    return None  # Allow download
            
            common_opts['match_filter'] = sanitize_and_filter
            
            # Add user's custom yt-dlp arguments
            user_args = {}  # per-user yt-dlp args were removed
            
            # Configure subtitle options based on user settings
            common_opts['writesubtitles'] = False
            common_opts['writeautomaticsub'] = False
            
            # Log final yt-dlp options for debugging
            
            # Check if we need to use --no-cookies for this domain
            if is_no_cookie_domain(url):
                common_opts['cookiefile'] = None  # Equivalent to --no-cookies
                logger.info(f"Using --no-cookies for domain: {url}")
            else:
                # Check if cookie.txt exists in download directory first, then user's folder
                download_cookie_path = os.path.join(user_dir_name, "cookie.txt")
                user_cookie_path = os.path.join("users", str(user_id), "cookie.txt")
                
                # YouTube: no cookie first (works from most IPs, a stale cookie itself triggers the bot check);
                # Cookie Manager cookies are tried by the automatic cookie retry only if this fails
                if is_youtube_url(url):
                    from COMMANDS import cookie_store
                    common_opts['cookiefile'] = cookie_store.first_active("youtube")  # admin's Cookie Manager cookie, else none
                    logger.info(f"YouTube cookie for user {user_id}: {bool(common_opts['cookiefile'])}")
                else:
                    # For non-YouTube URLs, use new cookie fallback system
                    from COMMANDS.cookies_cmd import get_cookie_cache_result, try_non_youtube_cookie_fallback
                    cache_result = get_cookie_cache_result(user_id, url)
                    
                    if cache_result and cache_result['result']:
                        # Use cached successful cookies
                        common_opts['cookiefile'] = cache_result['cookie_path']
                        logger.info(f"Using cached cookies for non-YouTube URL: {url}")
                    else:
                        # Try user cookies first
                        if os.path.exists(user_cookie_path):
                            common_opts['cookiefile'] = user_cookie_path
                            logger.info(f"Using user cookies for non-YouTube URL: {url}")
                        else:
                            # No user cookies: the admin's shared cookie for this site, else fallback during download
                            from COMMANDS.cookies_cmd import shared_cookie_path
                            common_opts['cookiefile'] = shared_cookie_path(url)
                            logger.info(f"No user cookies for non-YouTube URL: {url}, shared cookie: {bool(common_opts['cookiefile'])}")
            
            # If this is not a playlist with a range, add --no-playlist to the URL with the list parameter
            if not is_playlist and 'list=' in url:
                common_opts['noplaylist'] = True
            
            # Always use progress_hooks, even for HLS
            common_opts['progress_hooks'] = [progress_func]
            # Respect MKV toggle: remux to mkv when MKV is ON; otherwise prefer mp4
            mkv_on = False  # always MP4 (the old /format MKV switch is gone)

            # Adjust attempts' merge_output_format based on WEBM preference
            try:
                pass
            except Exception:
                pass

            ytdl_opts = {**common_opts, **attempt_opts}
            
            # Add proxy configuration if needed
            if use_proxy:
                # Force proxy for this download
                from COMMANDS.proxy_cmd import get_proxy_config
                proxy_config = get_proxy_config()
                
                if proxy_config and 'type' in proxy_config and 'ip' in proxy_config and 'port' in proxy_config:
                    # Build proxy URL
                    if proxy_config['type'] == 'http':
                        if proxy_config.get('user') and proxy_config.get('password'):
                            proxy_url = f"http://{proxy_config['user']}:{proxy_config['password']}@{proxy_config['ip']}:{proxy_config['port']}"
                        else:
                            proxy_url = f"http://{proxy_config['ip']}:{proxy_config['port']}"
                    elif proxy_config['type'] == 'https':
                        if proxy_config.get('user') and proxy_config.get('password'):
                            proxy_url = f"https://{proxy_config['user']}:{proxy_config['password']}@{proxy_config['ip']}:{proxy_config['port']}"
                        else:
                            proxy_url = f"https://{proxy_config['ip']}:{proxy_config['port']}"
                    elif proxy_config['type'] in ['socks4', 'socks5', 'socks5h']:
                        if proxy_config.get('user') and proxy_config.get('password'):
                            proxy_url = f"{proxy_config['type']}://{proxy_config['user']}:{proxy_config['password']}@{proxy_config['ip']}:{proxy_config['port']}"
                        else:
                            proxy_url = f"{proxy_config['type']}://{proxy_config['ip']}:{proxy_config['port']}"
                    else:
                        if proxy_config.get('user') and proxy_config.get('password'):
                            proxy_url = f"http://{proxy_config['user']}:{proxy_config['password']}@{proxy_config['ip']}:{proxy_config['port']}"
                        else:
                            proxy_url = f"http://{proxy_config['ip']}:{proxy_config['port']}"
                    
                    ytdl_opts['proxy'] = proxy_url
                    logger.info(f"Force using proxy for download: {proxy_url}")
                else:
                    logger.warning("Proxy requested but proxy configuration is incomplete")
            else:
                # Add proxy configuration if needed for this domain
                from HELPERS.proxy_helper import add_proxy_to_ytdl_opts
                ytdl_opts = add_proxy_to_ytdl_opts(ytdl_opts, url, user_id)
            
            # Add PO token provider for YouTube domains
            ytdl_opts = add_pot_to_ytdl_opts(ytdl_opts, url)
            
            # If MKV is ON, remux to mkv; else to mp4
            try:
                logger.info(f"Starting yt-dlp extraction for URL: {url}")
                logger.info(f"yt-dlp options: {ytdl_opts}")
                
                # First, check if the requested format is available using cached info or get_video_formats
                check_info = None
                if cached_video_info:
                    check_info = cached_video_info
                    logger.info("✅ [OPTIMIZATION] Using cached video info for format check")
                else:
                    from DOWN_AND_UP.yt_dlp_hook import get_video_formats
                    logger.info("Checking available formats...")
                    check_info = get_video_formats(url, user_id, cookies_already_checked=cookies_already_checked, use_proxy=use_proxy)
                    logger.info("Format check completed")
                
                # Check if requested format exists
                requested_format = attempt_opts.get('format', '')
                if requested_format and requested_format != 'best':
                    available_formats = check_info.get('formats', [])
                    format_found = False
                    
                    # Check if requested format is available
                    if requested_format.startswith('id:'):
                        # Check for specific format ID
                        requested_id = requested_format.split(':', 1)[1]
                        for fmt in available_formats:
                            if fmt.get('format_id') == requested_id:
                                format_found = True
                                logger.info(f"Format ID {requested_id} found: {fmt.get('ext', 'unknown')} {fmt.get('resolution', 'unknown')}")
                                break
                        
                        if not format_found:
                            logger.warning(f"Format ID {requested_id} not found for this video")
                            # Notify user and stop download
                            try:
                                available_ids = [fmt.get('format_id', 'unknown') for fmt in available_formats[:10]]
                                logger.info(f"Available format IDs: {available_ids}")
                                send_error_to_user(
                                    message,
                                    safe_get_messages(user_id).FORMAT_ID_NOT_FOUND_MSG.format(format_id=requested_id, available_ids=', '.join(available_ids[:10])) +
                                    f"Use /list command to see all available formats."
                                )
                                return None
                            except Exception as e:
                                logger.error(f"Error sending format not found message: {e}")
                            return None
                    elif 'av01' in requested_format:
                        # Check for AV1 format specifically
                        for fmt in available_formats:
                            vcodec = fmt.get('vcodec')
                            if vcodec and vcodec.startswith('av01'):
                                format_found = True
                                break
                        
                        if not format_found:
                            logger.warning(f"AV1 format requested but not available for this video")
                            
                            # Also check if there are any video formats at all
                            video_formats = [fmt for fmt in available_formats if fmt.get('vcodec') and not fmt.get('vcodec').startswith('images')]
                            if not video_formats:
                                logger.warning(f"No video formats available at all for this video")
                            # Notify user and stop download
                            try:
                                # Filter out non-video formats (like storyboards)
                                video_formats = [fmt for fmt in available_formats if fmt.get('vcodec') and not fmt.get('vcodec').startswith('images')]
                                
                                available_formats_list = []
                                for fmt in video_formats[:5]:
                                    vcodec = fmt.get('vcodec', 'unknown')
                                    height = fmt.get('height', 'unknown')
                                    if vcodec and vcodec != 'unknown':
                                        available_formats_list.append(f"• {vcodec} {height}p")
                                
                                formats_text = "\n".join(available_formats_list) if available_formats_list else "• No video formats available"
                                
                                safe_edit_message_text(user_id, proc_msg_id, 
                                    f"{current_total_process}\n{safe_get_messages(user_id).DOWN_UP_AV1_NOT_AVAILABLE_MSG.format(formats_text=formats_text)}")
                            except Exception as e:
                                logger.error(f"Failed to notify user about format unavailability: {e}")
                            
                            # Send error message to user
                            # Filter out non-video formats (like storyboards)
                            video_formats = [fmt for fmt in available_formats if fmt.get('vcodec') and not fmt.get('vcodec').startswith('images')]
                            
                            available_formats_list = []
                            for fmt in video_formats[:5]:
                                vcodec = fmt.get('vcodec', 'unknown')
                                height = fmt.get('height', 'unknown')
                                if vcodec and vcodec != 'unknown':
                                    available_formats_list.append(f"• {vcodec} {height}p")
                            
                            formats_text = "\n".join(available_formats_list) if available_formats_list else "• No video formats available"
                            
                            send_to_user(message, 
                                safe_get_messages(user_id).AV1_FORMAT_NOT_AVAILABLE_MSG.format(formats_text=formats_text) +
                                safe_get_messages(user_id).AV1_NOT_AVAILABLE_FORMAT_SELECT_MSG)
                            
                            return None
                
                # Try with proxy fallback if user proxy is enabled
                def extract_info_operation(opts):
                    messages = safe_get_messages(message.chat.id)
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        logger.info("yt-dlp instance created, starting extract_info...")
                        info_dict = ydl.extract_info(url, download=False)
                        logger.info("extract_info completed successfully")
                        return info_dict
                
                from HELPERS.proxy_helper import try_with_proxy_fallback
                info_dict = try_with_proxy_fallback(ytdl_opts, url, user_id, extract_info_operation)
                if info_dict is None:
                    raise Exception("Failed to extract video information with all available proxies")
                # Normalize info_dict to a dict
                if isinstance(info_dict, list):
                    info_dict = (info_dict[0] if len(info_dict) > 0 else {})
                if isinstance(info_dict, dict) and "entries" in info_dict:
                    entries = info_dict["entries"]
                    if not entries:
                        raise Exception(f"No videos found in playlist at index {current_index}")
                    # For a negative-index range, process all items
                    if use_range_download and len(entries) > 1:
                        # Process all items from the range.
                        # entries already contains all items in the range.
                        # They will be handled in the loop below.
                        pass  # We'll handle in the main loop
                    elif len(entries) > 1:  # If the video in the playlist is more than one
                        if current_index and current_index < len(entries):
                            info_dict = entries[current_index]
                        else:
                            raise Exception(f"Video index {current_index} out of range (total {len(entries)})")
                    else:
                        # If there is only one video in the playlist, just download it
                        info_dict = entries[0]  # Just take the first video

                # Check if this is a live stream and handle it if detection is disabled
                if info_dict and isinstance(info_dict, dict) and info_dict.get('is_live', False):
                    if not LimitsConfig.ENABLE_LIVE_STREAM_BLOCKING:
                        logger.info(f"Live stream detected but detection is disabled, using live stream downloader for user {user_id}: {url}")
                        from DOWN_AND_UP.live_stream_downloader import download_live_stream_chunked
                        result = download_live_stream_chunked(
                            app, message, url, user_id, user_dir_name, info_dict,
                            proc_msg_id, current_total_process, tags_text,
                            cookies_already_checked, use_proxy,
                            format_override=format_override, quality_key=quality_key
                        )
                        if result:
                            return info_dict
                        else:
                            logger.error("Live stream download failed")
                            return None

                # Detect HLS not only by URL/top-level protocol, but by requested formats too
                requested_formats = []
                try:
                    requested_formats = info_dict.get('requested_formats') or []
                except Exception:
                    requested_formats = []

                hls_in_requested = False
                for _rf in requested_formats:
                    proto = (_rf.get('protocol') or '').lower()
                    if 'm3u8' in proto or 'hls' in proto:
                        hls_in_requested = True
                        break

                if ("m3u8" in url.lower()) or (info_dict and info_dict.get("protocol") in ("m3u8", "m3u8_native")) or hls_in_requested:
                    is_hls = True
                    # Force ffmpeg for HLS and disable chunked HTTP to avoid Conflicting range on fragments
                    ytdl_opts["downloader"] = "ffmpeg"
                    ytdl_opts["hls_prefer_native"] = False
                    ytdl_opts["hls_use_mpegts"] = True
                    ytdl_opts.pop("http_chunk_size", None)
                    # Reduce parallelism for fragile HLS endpoints
                    ytdl_opts["concurrent_fragment_downloads"] = 1
                try:
                    if is_hls:
                        safe_edit_message_text(user_id, proc_msg_id,
                            f"{current_total_process}\n<i>Detected HLS stream.\n{safe_get_messages(user_id).ALWAYS_ASK_DOWNLOADING_HLS_MSG}</i>")
                    else:
                        safe_edit_message_text(user_id, proc_msg_id,
                            f"{current_total_process}\n> <i>{safe_get_messages(user_id).ALWAYS_ASK_DOWNLOADING_FORMAT_USING_MSG} {ytdl_opts.get('format', 'default')}...</i>")
                except Exception as e:
                    logger.error(f"Status update error: {e}")
                    # Check if error is related to quality_key
                    if "'quality_key'" in str(e):
                        _handle_quality_key_error(e, split_msg_ids, is_playlist, successful_uploads, indices_to_download, video_count, user_id, proc_msg_id, message, app)
                
                logger.info("Starting download phase...")
                # Try with proxy fallback if user proxy is enabled
                def download_operation(opts):
                    messages = safe_get_messages(user_id)
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        if is_hls:
                            # For HLS, start cycle progress as fallback, but progress_func will override it if percentages are available
                            cycle_stop = threading.Event()
                            progress_data = {'downloaded_bytes': 0, 'total_bytes': 0}
                            cycle_thread = start_cycle_progress(user_id, proc_msg_id, current_total_process, user_dir_name, cycle_stop, progress_data)
                            # Pass cycle_stop and progress_data to progress_func so it can update the cycle animation
                            progress_func.cycle_stop = cycle_stop
                            progress_func.progress_data = progress_data
                            try:
                                ydl.download([url])
                            finally:
                                cycle_stop.set()
                                cycle_thread.join(timeout=1)
                        elif opts.get('external_downloader'):  # aria2c: progress from the file on disk
                            from HELPERS.download_status import start_file_progress
                            fmt = selected_format or {}
                            total_guess = fmt.get('filesize') or fmt.get('filesize_approx') or 0
                            file_stop = threading.Event()
                            start_file_progress(user_id, proc_msg_id, user_dir_name, total_guess, file_stop)
                            try:
                                ydl.download([url])
                            finally:
                                file_stop.set()
                        else:
                            ydl.download([url])
                        return True
                
                from HELPERS.proxy_helper import try_with_proxy_fallback
                result = try_with_proxy_fallback(ytdl_opts, url, user_id, download_operation)
                if result is None:
                    raise Exception("Failed to download video with all available proxies")
                try:
                    safe_edit_message_text(user_id, proc_msg_id, safe_get_messages(user_id).VIDEO_DOWNLOAD_COMPLETE_MSG.format(process=current_total_process, bar=full_bar))
                except Exception as e:
                    logger.error(f"Final progress update error: {e}")
                
                logger.info("Download completed successfully")
                
                # Cache successful cookie result for future use
                if not is_youtube_url(url):
                    from COMMANDS.cookies_cmd import set_cookie_cache_result
                    cookie_file_path = ytdl_opts.get('cookiefile')
                    if cookie_file_path and os.path.exists(cookie_file_path):
                        set_cookie_cache_result(user_id, url, True, cookie_file_path)
                        logger.info(f"Cached successful cookie result for {url}")
                
                # Remove protection file after successful download
                from HELPERS.filesystem_hlp import remove_protection_file
                remove_protection_file(user_dir_name)
                
                return info_dict
            except yt_dlp.utils.DownloadError as e:
                nonlocal error_message
                from HELPERS.cancel import is_set as _cancelled
                if _cancelled(user_id):
                    return "STOP"
                error_message = str(e)
                logger.error(f"DownloadError: {error_message}")
                
                # Check for live stream detection (only if detection is enabled)
                if "LIVE_STREAM_DETECTED" in error_message:
                    if LimitsConfig.ENABLE_LIVE_STREAM_BLOCKING:
                        live_stream_message = (
                            safe_get_messages(user_id).LIVE_STREAM_DETECTED_MSG +
                            "• You can see the final video length\n\n"
                            "Once the stream is completed, you'll be able to download it as a regular video."
                        )
                        send_error_to_user(message, live_stream_message)
                        return "LIVE_STREAM"
                    # If detection is disabled, continue with live stream download
                    # This will be handled by the live stream download function
                
                # Check for postprocessing errors
                if "Postprocessing" in error_message and "Error opening output files" in error_message:
                    postprocessing_message = (
                        safe_get_messages(user_id).FILE_PROCESSING_ERROR_INVALID_CHARS_MSG +
                        "**Solutions:**\n"
                        "• Try downloading again - the system will use a safer filename\n"
                        "• If the problem persists, the video title may contain unsupported characters\n"
                        "• Consider using a different video source if available\n\n"
                        "The download will be retried automatically with a cleaned filename."
                    )
                    send_error_to_user(message, postprocessing_message)
                    logger.error(f"Postprocessing error: {error_message}")
                    return "POSTPROCESSING_ERROR"
                
                # Check for postprocessing errors with Invalid argument
                if "Postprocessing" in error_message and "Invalid argument" in error_message:
                    logger.error(f"Postprocessing error (Invalid argument): {error_message}")
                    return "POSTPROCESSING_ERROR"
                
                # Check for format not available error
                if "Requested format is not available" in error_message:
                    logger.error(f"Format not available error: {error_message}")
                    return "FORMAT_NOT_AVAILABLE"
                
                
                
                # Auto-fallback to gallery-dl (/img) for all supported errors
                if should_fallback_to_gallery_dl(error_message, url):
                    try:
                        from COMMANDS.image_cmd import image_command
                        from HELPERS.safe_messeger import fake_message
                    except Exception as imp_e:
                        logger.error(f"Failed to import gallery-dl fallback handlers: {imp_e}")
                    else:
                        try:
                            safe_edit_message_text(user_id, proc_msg_id,
                                f"{current_total_process}\n🔄 yt-dlp failed, trying gallery-dl…")
                        except Exception:
                            pass
                        try:
                            # Check if content is NSFW for fallback
                            from HELPERS.porn import is_porn
                            is_nsfw = is_porn(url, "", "", None) or user_forced_nsfw
                            logger.info(f"[FALLBACK] is_porn check for {url}: {is_porn(url, '', '', None)}, user_forced_nsfw: {user_forced_nsfw}, final is_nsfw: {is_nsfw}")
                            
                            # STRICT: use the saved original text with the range
                            logger.info(f"[FALLBACK DEBUG] Using saved original_message_text: {original_message_text}")
                            
                            # Look for a URL with *start*end range
                            import re
                            range_url_match = re.search(r'(https?://[^\s\*#]+)\*(\d+)\*(\d+)', original_message_text)
                            if range_url_match:
                                parsed_url = range_url_match.group(1)
                                start_range = int(range_url_match.group(2))
                                end_range = int(range_url_match.group(3))
                                logger.info(f"[FALLBACK DEBUG] FOUND RANGE: {parsed_url} with range {start_range}-{end_range}")
                            else:
                                # Fallback to a regular URL
                                m = re.search(r'https?://[^\s\*#]+', original_message_text)
                                parsed_url = m.group(0) if m else original_message_text
                                start_range = 1
                                end_range = 1
                                logger.info(f"[FALLBACK DEBUG] NO RANGE FOUND, using url: {parsed_url}")
                            
                            # Build fallback command for single item only (not entire range)
                            # Use current_index instead of full range to download only the failed item
                            current_item_index = current_index + 1  # current_index is 0-based, we need 1-based
                            fallback_text = f"/img {current_item_index}-{current_item_index} {parsed_url}"
                            logger.info(f"[FALLBACK] Downloading only failed item {current_item_index} via gallery-dl, fallback_text: {fallback_text}")
                            
                            if tags_text:
                                fallback_text += f" {tags_text}"
                            
                            # Add NSFW tag if content is detected as NSFW
                            if is_nsfw and "#nsfw" not in fallback_text.lower():
                                fallback_text += " #nsfw"
                                logger.info(f"[FALLBACK] Added #nsfw tag for NSFW content: {url}")
                            
                            # For groups, preserve original chat_id and message_thread_id
                            original_chat_id = message.chat.id if hasattr(message, 'chat') else user_id
                            message_thread_id = getattr(message, 'message_thread_id', None) if hasattr(message, 'message_thread_id') else None
                            image_command(app, fake_message(fallback_text, user_id, original_chat_id=original_chat_id, message_thread_id=message_thread_id, original_message=message))
                            logger.info(f"Triggered gallery-dl fallback via /img, is_nsfw={is_nsfw}, range={start_range}-{end_range}")
                            return "IMG"
                        except Exception as call_e:
                            logger.error(f"Failed to trigger gallery-dl fallback: {call_e}")
                
                # Check whether the error is related to YouTube geo restrictions
                if is_youtube_url(url):
                    if is_youtube_geo_error(error_message) and not did_proxy_retry:
                        logger.info(f"YouTube geo-blocked error detected for user {user_id}, attempting retry with proxy")
                        
                        # Try downloading via proxy
                        retry_result = retry_download_with_proxy(
                            user_id, url, try_download, url, attempt_opts
                        )
                        
                        if retry_result is not None:
                            logger.info(f"Download retry with proxy successful for user {user_id}")
                            did_proxy_retry = True
                            return retry_result
                        else:
                            logger.warning(f"Download retry with proxy failed for user {user_id}")
                            did_proxy_retry = True
                else:
                    # For non-YouTube sites, try cookie rotation
                    logger.info(f"Non-YouTube download error detected for user {user_id}, attempting cookie fallback")
                    
                    # Check whether the error is cookie-related
                    error_str = error_message.lower()
                    if any(keyword in error_str for keyword in ['cookie', 'auth', 'login', 'sign in', '403', '401', 'forbidden', 'unauthorized']):
                        logger.info(f"Error appears to be cookie-related for {url}, trying cookie fallback")
                        
                        # Try cookie rotation with the new system
                        from COMMANDS.cookies_cmd import try_non_youtube_cookie_fallback
                        retry_result = try_non_youtube_cookie_fallback(
                            user_id, url, try_download, url, attempt_opts
                        )
                        
                        if retry_result is not None:
                            logger.info(f"Download retry with cookie fallback successful for user {user_id}")
                            return retry_result
                        else:
                            logger.warning(f"Download retry with cookie fallback failed for user {user_id}")
                    else:
                        logger.info(f"Error appears to be non-cookie-related for {url}, skipping cookie fallback")
                
                # Send full error message with instructions immediately (only once)
                if not error_message_sent:
                    # Extract error code and description from yt-dlp error
                    error_code = "UNKNOWN_ERROR"
                    error_description = error_message
                    
                    # Try to extract specific error codes
                    if "HTTP Error 403" in error_message:
                        error_code = "HTTP_403_FORBIDDEN"
                        error_description = "Access forbidden - may need cookies or authentication"
                    elif "HTTP Error 401" in error_message:
                        error_code = "HTTP_401_UNAUTHORIZED"
                        error_description = "Authentication required - cookies needed"
                    elif "Video unavailable" in error_message:
                        error_code = "VIDEO_UNAVAILABLE"
                        error_description = "Video is not available or has been removed"
                    elif "Private video" in error_message:
                        error_code = "PRIVATE_VIDEO"
                        error_description = "Video is private and requires authentication"
                    elif "Sign in to confirm" in error_message:
                        error_code = "SIGN_IN_REQUIRED"
                        error_description = "Sign in required - cookies needed"
                        # Auto rotate IP on SIGN_IN_REQUIRED
                        try:
                            from services.system_service import rotate_ip
                            logger.warning(f"Auto-rotating IP due to SIGN_IN_REQUIRED error for user {user_id}")
                            rotate_result = rotate_ip()
                            if rotate_result.get("status") == "ok":
                                logger.info(f"IP rotated successfully: IPv4={rotate_result.get('ipv4')}, IPv6={rotate_result.get('ipv6')}")
                            else:
                                logger.error(f"Failed to auto-rotate IP: {rotate_result.get('message')}")
                        except Exception as rotate_error:
                            logger.error(f"Error during auto-rotate IP: {rotate_error}")
                    elif "No video formats found" in error_message:
                        error_code = "NO_FORMATS"
                        error_description = "No downloadable formats available"
                    elif "Unsupported URL" in error_message:
                        error_code = "UNSUPPORTED_URL"
                        error_description = "This URL is not supported by yt-dlp"
                    elif "Network error" in error_message:
                        error_code = "NETWORK_ERROR"
                        error_description = "Network connection failed"
                    elif "ffmpeg exited with code" in error_message or "ERROR: ffmpeg" in error_message:
                        error_code = "FFMPEG_ERROR"
                        # Try to extract more details from error message
                        if "code 1" in error_message:
                            error_description = "FFmpeg processing failed - video format may be incompatible or corrupted"
                        elif "code 2" in error_message:
                            error_description = "FFmpeg error - invalid arguments or unsupported format"
                        else:
                            error_description = "FFmpeg processing error occurred"
                        
                        # Try to extract specific error details
                        import re
                        ffmpeg_details = re.search(r'ffmpeg.*?error[:\s]+(.*?)(?:\n|$)', error_message, re.IGNORECASE | re.DOTALL)
                        if ffmpeg_details:
                            details = ffmpeg_details.group(1).strip()[:200]
                            if details:
                                error_description += f"\n\nDetails: {details}"
                        
                        # Suggest solutions
                        error_description += (
                            "\n\n**Possible solutions:**\n"
                            "• Try downloading with a different quality/format\n"
                            "• The video may be corrupted or in an unsupported format\n"
                            "• Try downloading without post-processing\n"
                            "• Check if ffmpeg is properly installed"
                        )
                    
                    send_error_to_user(
                        message,                   
                        f"<blockquote>{safe_get_messages(user_id).ERROR_CHECK_SUPPORTED_SITES_MSG}</blockquote>\n"
                        f"<blockquote>{safe_get_messages(user_id).ERROR_COOKIE_NEEDED_MSG}</blockquote>\n"
                        f"<blockquote>{safe_get_messages(user_id).ERROR_COOKIE_INSTRUCTIONS_MSG}</blockquote>\n"
                        f"────────────────\n"
                        f"❌ <b>Error Code:</b> <code>{error_code}</code>\n"
                        f"📝 <b>Description:</b> {error_description}\n"
                        f"🔧 <b>Full Error:</b> <code>{error_message}</code>"
                    )
                    error_message_sent = True
                return None
            except Exception as e:
                from HELPERS.cancel import is_set as _cancelled
                if _cancelled(user_id):
                    return "STOP"
                error_message = str(e)
                logger.error(f"Attempt with format {ytdl_opts.get('format', 'default')} failed: {e}")
                # Auto-fallback to gallery-dl for obvious non-video cases
                emsg = str(e)
                if (
                    "No videos found in playlist" in emsg
                    or "Unsupported URL" in emsg
                ):
                    try:
                        from COMMANDS.image_cmd import image_command
                        from HELPERS.safe_messeger import fake_message
                    except Exception as imp_e:
                        logger.error(f"Failed to import gallery-dl fallback handlers (generic): {imp_e}")
                    else:
                        try:
                            safe_edit_message_text(user_id, proc_msg_id,
                                f"{current_total_process}\n🔄 yt-dlp failed, trying gallery-dl…")
                        except Exception:
                            pass
                        try:
                            # Check if content is NSFW for fallback
                            from HELPERS.porn import is_porn
                            is_nsfw = is_porn(url, "", "", None) or user_forced_nsfw
                            logger.info(f"[FALLBACK] is_porn check for {url}: {is_porn(url, '', '', None)}, user_forced_nsfw: {user_forced_nsfw}, final is_nsfw: {is_nsfw}")
                            
                            # STRICT: use the saved original text with the range
                            logger.info(f"[FALLBACK DEBUG] Using saved original_message_text: {original_message_text}")
                            
                            # Look for a URL with *start*end range
                            import re
                            range_url_match = re.search(r'(https?://[^\s\*#]+)\*(\d+)\*(\d+)', original_message_text)
                            if range_url_match:
                                parsed_url = range_url_match.group(1)
                                start_range = int(range_url_match.group(2))
                                end_range = int(range_url_match.group(3))
                                logger.info(f"[FALLBACK DEBUG] FOUND RANGE: {parsed_url} with range {start_range}-{end_range}")
                            else:
                                # Fallback to a regular URL
                                m = re.search(r'https?://[^\s\*#]+', original_message_text)
                                parsed_url = m.group(0) if m else original_message_text
                                start_range = 1
                                end_range = 1
                                logger.info(f"[FALLBACK DEBUG] NO RANGE FOUND, using url: {parsed_url}")
                            
                            # Build fallback command for single item only (not entire range)
                            # Use current_index instead of full range to download only the failed item
                            current_item_index = current_index + 1  # current_index is 0-based, we need 1-based
                            fallback_text = f"/img {current_item_index}-{current_item_index} {parsed_url}"
                            logger.info(f"[FALLBACK] Downloading only failed item {current_item_index} via gallery-dl, fallback_text: {fallback_text}")
                            
                            if tags_text:
                                fallback_text += f" {tags_text}"
                            
                            # Add NSFW tag if content is detected as NSFW
                            if is_nsfw and "#nsfw" not in fallback_text.lower():
                                fallback_text += " #nsfw"
                                logger.info(f"[FALLBACK] Added #nsfw tag for NSFW content: {url}")
                            
                            # For groups, preserve original chat_id and message_thread_id
                            original_chat_id = message.chat.id if hasattr(message, 'chat') else user_id
                            message_thread_id = getattr(message, 'message_thread_id', None) if hasattr(message, 'message_thread_id') else None
                            image_command(app, fake_message(fallback_text, user_id, original_chat_id=original_chat_id, message_thread_id=message_thread_id, original_message=message))
                            logger.info(f"Triggered gallery-dl fallback via /img (generic), is_nsfw={is_nsfw}, range={start_range}-{end_range}")
                            return "IMG"
                        except Exception as call_e:
                            logger.error(f"Failed to trigger gallery-dl fallback (generic): {call_e}")
				
                # Check if this is a "No videos found in playlist" error
                if "No videos found in playlist" in str(e):
                    error_message = safe_get_messages(user_id).DOWN_UP_NO_VIDEOS_PLAYLIST_MSG.format(index=current_index + 1)
                    send_error_to_user(message, error_message)
                    logger.info(f"Stopping download: playlist item at index {current_index} (no video found)")
                    return "STOP"  # New special value for full stop
                
                # Check if this is a TikTok infinite loop error
                if "TikTok API keeps sending the same page" in str(e) and "infinite loop" in str(e):
                    error_message = safe_get_messages(user_id).VIDEO_TIKTOK_API_ERROR_SKIP_MSG.format(index=current_index + 1)
                    send_to_user(message, error_message)
                    logger.info(f"Skipping TikTok video at index {current_index} due to API error")
                    return "SKIP"  # Skip this video and continue with next

                send_to_user(message, safe_get_messages(user_id).UNKNOWN_ERROR_MSG.format(error=e))
                return None

        # For negative indices, use the whole range at once (not a loop)
        # use_range_download was declared earlier (line 325)
        total_playlist_count = None  # Total videos in playlist (for converting negative indices)
        playlist_range_str = None  # Playlist range string (e.g., "1:7" or "1:7:-1")
        has_negative_indices_for_download = False  # Flag for negative indices (do not use range_entries_metadata)
        if is_playlist and video_start_with is not None and video_end_with is not None:
            if video_start_with < 0 or video_end_with < 0:
                use_range_download = True
                has_negative_indices_for_download = True  # For negative indices, download each one separately
                # playlist_range_str is not used for negative indices, since we process each index separately
                # For negative indices we need the total number of videos in the playlist
                # Do a preliminary request to get the total video count
                try:
                    from DOWN_AND_UP.yt_dlp_hook import get_video_formats
                    logger.info(f"Getting total playlist count for negative indices conversion...")
                    temp_info = get_video_formats(url, user_id, 1, cookies_already_checked, use_proxy, 1)
                    if temp_info and isinstance(temp_info, dict):
                        if "entries" in temp_info:
                            total_playlist_count = len(temp_info["entries"])
                        elif "_playlist_entries" in temp_info:
                            total_playlist_count = len(temp_info["_playlist_entries"])
                    if total_playlist_count:
                        logger.info(f"Total playlist count: {total_playlist_count}")
                        # Convert negative indices to positive:
                        # -1 = last video (total_playlist_count), -2 = second-to-last (total_playlist_count - 1), etc.
                        # Formula: positive_index = total_playlist_count + negative_index + 1
                        converted_indices = []
                        for neg_idx in playlist_indices_all:
                            if neg_idx < 0:
                                pos_idx = total_playlist_count + neg_idx + 1
                                converted_indices.append(pos_idx)
                            else:
                                converted_indices.append(neg_idx)
                        # Sort in reverse order to download from last to first
                        converted_indices.sort(reverse=True)
                        playlist_indices_all = converted_indices
                        logger.info(f"Converted negative indices to positive: {converted_indices}")
                except Exception as e:
                    logger.warning(f"Failed to get total playlist count for negative indices: {e}, using original indices")
            elif video_start_with != video_end_with:
                # Build range string for normal cases
                if is_reverse_order:
                    playlist_range_str = f"{video_start_with}:{video_end_with}:-1"
                else:
                    playlist_range_str = f"{video_start_with}:{video_end_with}"
        
        if use_range_download:
            # For negative indices, use the whole range at once.
            # indices_to_download already contains converted positive indices.
            # For negative indices, always use reverse order (last to first).
            # playlist_range_str is not used for negative indices, since we process each index separately.
            # Process each index from the original list in reverse order (last to first).
            indices_to_download = playlist_indices_all  # Already sorted in reverse order
        elif is_playlist and safe_quality_key:
            indices_to_download = uncached_indices
        elif is_playlist:
            indices_to_download = playlist_indices_all
        else:
            indices_to_download = range(video_count)
        
        range_entries_metadata = None
        current_playlist_items_override = None
        logger.info(f"🔍 [DEBUG] Starting playlist download: indices_to_download={indices_to_download}, len={len(indices_to_download) if indices_to_download else 0}, use_range_download={use_range_download}, has_negative_indices_for_download={has_negative_indices_for_download}")
        for idx, current_index in enumerate(indices_to_download):
            logger.info(f"🔍 [DEBUG] Processing video {idx + 1}/{len(indices_to_download)}: current_index={current_index}")
            messages = safe_get_messages(message.chat.id)
            total_process = f"""
<b>📶 {safe_get_messages(user_id).TOTAL_PROGRESS_MSG}</b>
<blockquote>{safe_get_messages(user_id).VIDEO_PROGRESS_MSG.format(current=idx + 1, total=len(indices_to_download))}</blockquote>
"""
            current_total_process = total_process

            # Determine rename_name based on the incoming playlist_name:
            if playlist_name and playlist_name.strip():
                # A new name for the playlist is explicitly set - let's use it
                rename_name = sanitize_filename_strict(f"{playlist_name.strip()} - Part {idx + 1}")
            else:
                # No new name set - extract name from metadata
                rename_name = None

            # Reset retry flags for each new item in playlist
            did_cookie_retry = False
            did_proxy_retry = False
            error_message_sent = False  # Reset error message flag for each playlist item

            info_dict = None
            skip_item = False
            stop_all = False
            
            # Define safe filename template for fallback
            timestamp = int(time.time())
            safe_outtmpl = os.path.join(user_dir_name, f"download_{timestamp}.%(ext)s")
            
            # For negative indices, don't use reuse_range_download; download each index separately
            reuse_range_download = use_range_download and range_entries_metadata is not None and not has_negative_indices_for_download
            if reuse_range_download:
                if idx < len(range_entries_metadata):
                    info_dict = range_entries_metadata[idx]
                    logger.info(f"Reusing cached range download entry #{idx + 1} for playlist index {current_index}")
                else:
                    logger.warning(f"Range download already completed but entry #{idx + 1} missing (total {len(range_entries_metadata)}). Stopping playlist.")
                    info_dict = None
                    stop_all = True
            else:
                if use_range_download:
                    # For negative indices, playlist_range_str is None, so keep it None
                    # try_download will use current_index (already converted to positive)
                    current_playlist_items_override = playlist_range_str  # May be None for negative indices
                else:
                    current_playlist_items_override = None

                for attempt in attempts:
                    result = try_download(url, attempt)
                    
                    # If download failed and it's a YouTube URL, try automatic cookie retry
                    if result is None and is_youtube_url(url) and not did_cookie_retry:
                        logger.info(f"Video download failed for user {user_id}, attempting automatic cookie retry")
                        
                        # Try retry with different cookies
                        retry_result = retry_download_with_different_cookies(
                            user_id, url, try_download, url, attempt
                        )
                        
                        if retry_result is not None:
                            logger.info(f"Video download retry with different cookies successful for user {user_id}")
                            result = retry_result
                            did_cookie_retry = True
                        else:
                            logger.warning(f"All cookie retry attempts failed for user {user_id}")
                            did_cookie_retry = True
                    if result == "STOP":
                        stop_all = True
                        break
                    elif result == "SKIP":
                        skip_item = True
                        break
                    elif result == "IMG":
                        # Gallery-dl fallback has been triggered for this specific item
                        logger.info(f"Gallery-dl fallback triggered for item {current_index}, continuing with next item")
                        skip_item = True
                        break
                    elif result is not None and isinstance(result, dict):
                        info_dict = result
                        break
                    elif result is not None and isinstance(result, str):
                        # Handle string return values (like "POSTPROCESSING_ERROR")
                        logger.info(f"Download attempt returned string result: {result}")
                        if result == "POSTPROCESSING_ERROR":
                            # Try again with safe filename if this was the first attempt
                            if attempt == attempts[0]:  # First attempt failed
                                logger.info("First attempt failed with postprocessing error, retrying with safe filename")
                                # Modify the attempt to use safe filename
                                safe_attempt = attempt.copy()
                                safe_attempt['outtmpl'] = safe_outtmpl
                                safe_result = try_download(url, safe_attempt)
                                if safe_result is not None and isinstance(safe_result, dict):
                                    info_dict = safe_result
                                    break
                                elif safe_result is not None and isinstance(safe_result, str):
                                    logger.info(f"Safe filename attempt also failed: {safe_result}")
                                    continue
                            else:
                                # Already tried safe filename, skip this attempt
                                continue
                        else:
                            # Other string results, skip this attempt
                            continue

                current_playlist_items_override = None

                # For negative indices, don't use range_entries_metadata; download each index separately
                if use_range_download and info_dict is not None and not has_negative_indices_for_download:
                    entries_list = []
                    if isinstance(info_dict, dict) and "entries" in info_dict:
                        entries_list = info_dict.get("entries") or []
                    elif isinstance(info_dict, list):
                        entries_list = info_dict
                    else:
                        entries_list = [info_dict]

                    if not entries_list:
                        logger.warning("Range download completed but yt-dlp returned no entries.")
                        range_entries_metadata = []
                        info_dict = None
                    else:
                        range_entries_metadata = entries_list
                        if idx < len(range_entries_metadata):
                            info_dict = range_entries_metadata[idx]
                        else:
                            logger.warning(f"Range download returned {len(range_entries_metadata)} entries, but requested entry #{idx + 1} is missing.")
                            info_dict = None
                            stop_all = True

            if stop_all:
                logger.info(f"Stopping all downloads due to playlist error at index {current_index}")
                break

            if skip_item:
                logger.info(f"Skipping item at index {current_index} (no video content)")
                continue

            if info_dict is None:
                # Send error message to user only on final failure
                if error_message and not error_message_sent:
                    # Check for specific error types and send appropriate messages
                    if "Postprocessing" in error_message and "Invalid argument" in error_message:
                        send_error_to_user(message, "⚠️ <b>The video downloaded but couldn't be processed.</b>\n"
                                                    "<blockquote>Send the link again, or pick another quality.</blockquote>")
                        error_message_sent = True
                    elif "Requested format is not available" in error_message:
                        send_error_to_user(message, "⚠️ <b>This quality isn't available for this video.</b>\n"
                                                    "<blockquote>Send the link again and pick <b>Best Quality</b>.</blockquote>")
                        error_message_sent = True

                with playlist_errors_lock:
                    error_key = f"{user_id}_{playlist_name}"
                    if error_key not in playlist_errors:
                        playlist_errors[error_key] = True

                break

            successful_uploads += 1

            video_id = info_dict.get("id", None)
            # Get original title for caption (saved before sanitization in match_filter)
            original_video_title = info_dict.get("original_title", info_dict.get("title", None))  # Original title for caption
            full_video_title = info_dict.get("description", original_video_title)
            video_title = info_dict.get("title", "video")  # Already sanitized title for file operations
            video_page_url = (
                info_dict.get("webpage_url")
                or info_dict.get("original_url")
                or info_dict.get("url")
                or info_dict.get("canonical_url")
                or url
            )
            
            # Save the video's unique URL for later caching
            if is_playlist:
                playlist_video_urls[current_index] = video_page_url
                logger.info(f"🔍 [CACHE] Saved unique URL for video index={current_index}: {video_page_url}")

            # --- Use new centralized function for all tags ---
            tags_list = tags_text.split() if tags_text else []
            tags_text_final = generate_final_tags(url, tags_list, info_dict)
            save_user_tags(user_id, tags_text_final.split())

           # If rename_name is not set, set it equal to video_title
            if rename_name is None:
                rename_name = video_title

            dir_path = user_dir_name

            # Save the full name to a file
            full_title_path = os.path.join(dir_path, "full_title.txt")
            try:
                with open(full_title_path, "w", encoding="utf-8") as f:
                    f.write(full_video_title if full_video_title else original_video_title)
            except Exception as e:
                logger.error(f"Error saving full title: {e}")

            info_text = f"""
{total_process}
<b>{safe_get_messages(user_id).DOWN_UP_VIDEO_INFO_MSG}</b>
<blockquote><b>{safe_get_messages(user_id).DOWN_UP_NUMBER_MSG}:</b> {current_index}</blockquote>
<blockquote><b>{safe_get_messages(user_id).DOWN_UP_TITLE_MSG}:</b> {original_video_title}</blockquote>
<blockquote><b>{safe_get_messages(user_id).DOWN_UP_ID_MSG}:</b> {video_id}</blockquote>
"""

            try:
                safe_edit_message_text(user_id, proc_msg_id,
                    f"{info_text}\n{full_bar}   100.0%\n<i>{safe_get_messages(user_id).DOWN_UP_DOWNLOADED_VIDEO_MSG}\n{safe_get_messages(user_id).DOWN_UP_PROCESSING_UPLOAD_MSG}</i>")
            except Exception as e:
                logger.error(f"Status update error after download: {e}")
                # Check if error is related to quality_key
                if "'quality_key'" in str(e):
                    _handle_quality_key_error(e, split_msg_ids, is_playlist, successful_uploads, indices_to_download, video_count, user_id, proc_msg_id, message, app)

            dir_path = user_dir_name
            downloaded_file = None
            downloaded_abs_path = None
            
            # Get user's preferred video format to determine file extensions
            user_args = {}  # per-user yt-dlp args were removed
            user_video_format = user_args.get('video_format', 'mp4')
            user_merge_format = user_args.get('merge_output_format', 'mp4')
            
            # Determine which format to look for based on user preferences
            # Priority: video_format > merge_output_format > default
            target_format = user_video_format if user_video_format != 'mp4' else user_merge_format
            
            # Define supported video extensions based on user preference
            if target_format == 'mp4':
                video_extensions = ('.mp4', '.m4v')
            elif target_format == 'webm':
                video_extensions = ('.webm',)
            elif target_format == 'mkv':
                video_extensions = ('.mkv',)
            elif target_format == 'avi':
                video_extensions = ('.avi',)
            elif target_format == 'mov':
                video_extensions = ('.mov',)
            elif target_format == 'flv':
                video_extensions = ('.flv',)
            elif target_format == '3gp':
                video_extensions = ('.3gp', '.3g2')
            elif target_format == 'ogv':
                video_extensions = ('.ogv', '.ogg')
            elif target_format == 'wmv':
                video_extensions = ('.wmv',)
            elif target_format == 'asf':
                video_extensions = ('.asf',)
            else:
                # Fallback to all supported formats
                video_extensions = ('.mp4', '.mkv', '.webm', '.ts', '.avi', '.mov', '.flv', '.3gp', '.ogv', '.wmv', '.asf')
            
            # Try to use exact filename from yt-dlp metadata first
            filename_hints = []
            meta_filename = info_dict.get('_filename')
            if meta_filename:
                filename_hints.append(meta_filename)
            filepath_hint = info_dict.get('filepath')
            if filepath_hint:
                filename_hints.append(filepath_hint)
            requested_downloads = info_dict.get('requested_downloads') or []
            for rd in requested_downloads:
                rd_path = rd.get('filepath') or rd.get('_filename')
                if rd_path:
                    filename_hints.append(rd_path)
            
            for hint in filename_hints:
                if hint and os.path.exists(hint):
                    downloaded_abs_path = os.path.abspath(hint)
                    downloaded_file = os.path.basename(downloaded_abs_path)
                    logger.info(f"Using yt-dlp reported file path: {downloaded_abs_path}")
                    break
            
            if not downloaded_file:
                allfiles = os.listdir(dir_path)
                
                files = [fname for fname in allfiles if fname.endswith(video_extensions)]
                files.sort()
                
                # Log all found files for debugging
                logger.info(f"Found video files in {dir_path}: {files}")
                
                # If no files found with preferred format, try to find any video file
                if not files:
                    logger.warning(f"No files found with preferred format {target_format}, searching for any video file")
                    fallback_extensions = ('.mp4', '.mkv', '.webm', '.ts', '.avi', '.mov', '.flv', '.3gp', '.ogv', '.wmv', '.asf', '.m4v')
                    files = [fname for fname in allfiles if fname.endswith(fallback_extensions)]
                    files.sort()
                    logger.info(f"Found video files with fallback search: {files}")
                
                if not files:
                    send_error_to_user(message, safe_get_messages(user_id).SKIPPING_UNSUPPORTED_FILE_TYPE_MSG.format(index=current_index))
                    continue

                downloaded_file = files[0]
                downloaded_abs_path = os.path.abspath(os.path.join(dir_path, downloaded_file))
            
            logger.info(f"Selected downloaded file: {downloaded_file}")
            write_logs(message, url, downloaded_file)
            
            # Save original filename for subtitle search
            original_video_filename = downloaded_file
            original_video_path = downloaded_abs_path or os.path.join(dir_path, original_video_filename)

            if rename_name == video_title:
                caption_name = original_video_title  # Original title for caption
                # Clean up the filename by removing common subtitle indicators
                cleaned_title = original_video_title
                # Remove common subtitle indicators from the title
                subtitle_indicators = [
                    " (English subtitles)",
                    " (English)",
                    " (Subtitles)",
                    " (Subs)",
                    " [English subtitles]",
                    " [English]",
                    " [Subtitles]",
                    " [Subs]",
                    " - English subtitles",
                    " - English",
                    " - Subtitles",
                    " - Subs"
                ]
                for indicator in subtitle_indicators:
                    cleaned_title = cleaned_title.replace(indicator, "")
                
                # Use cleaned title for filename
                final_name = sanitize_filename_strict(cleaned_title + os.path.splitext(downloaded_file)[1])
                if final_name != downloaded_file:
                    old_path = downloaded_abs_path or os.path.join(dir_path, downloaded_file)
                    new_path = os.path.join(dir_path, final_name)
                    try:
                        if os.path.exists(new_path):
                            os.remove(new_path)
                        os.rename(old_path, new_path)
                    except Exception as e:
                        logger.error(f"Error renaming file from {old_path} to {new_path}: {e}")
                        final_name = downloaded_file
            else:
                ext = os.path.splitext(downloaded_file)[1]
                # Sanitize filename for disk storage while keeping original title for caption
                final_name = sanitize_filename(rename_name + ext)
                caption_name = rename_name  # Original title for caption
                old_path = downloaded_abs_path or os.path.join(dir_path, downloaded_file)
                new_path = os.path.join(dir_path, final_name)

                if os.path.exists(new_path):
                    try:
                        os.remove(new_path)
                    except Exception as e:
                        logger.error(f"Error removing existing file {new_path}: {e}")

                try:
                    os.rename(old_path, new_path)
                except Exception as e:
                    logger.error(f"Error renaming file from {old_path} to {new_path}: {e}")
                    final_name = downloaded_file
                    caption_name = original_video_title  # Original title for caption

            user_vid_path = os.path.join(dir_path, final_name)
            if final_name.lower().endswith((".webm", ".ts")):
                try:
                    safe_edit_message_text(user_id, proc_msg_id,
                        f"{info_text}\n{full_bar}   100.0%\nConverting video using ffmpeg... ⏳")
                except Exception as e:
                    logger.error(f"Error updating status before conversion: {e}")

                mp4_basename = sanitize_filename_strict(os.path.splitext(final_name)[0]) + ".mp4"
                mp4_file = os.path.join(dir_path, mp4_basename)

                # Get FFmpeg path using the common function
                from DOWN_AND_UP.ffmpeg import get_ffmpeg_path
                ffmpeg_path = get_ffmpeg_path()
                if not ffmpeg_path:
                    send_error_to_user(message, safe_get_messages(user_id).FFMPEG_NOT_FOUND_MSG)
                    break
                
                ffmpeg_cmd = [
                    ffmpeg_path,
                    "-y",
                    "-i", user_vid_path,
                    "-c:v", "libx264",
                    "-preset", "fast",
                    "-crf", "23",
                    "-c:a", "aac",
                    "-b:a", "128k",
                    mp4_file
                ]
                try:
                    result = subprocess.run(ffmpeg_cmd, check=True, capture_output=True, text=True, encoding='utf-8', errors='replace')
                    os.remove(user_vid_path)
                    user_vid_path = mp4_file
                    final_name = mp4_basename
                except subprocess.CalledProcessError as e:
                    error_details = f"Return code: {e.returncode}"
                    if e.stderr:
                        error_details += f"\nError output: {e.stderr[:500]}"
                    if e.stdout:
                        error_details += f"\nStandard output: {e.stdout[:500]}"
                    
                    # Check for specific FFmpeg errors
                    if "Invalid argument" in str(e.stderr):
                        error_message = safe_get_messages(user_id).DOWN_UP_VIDEO_CONVERSION_FAILED_INVALID_MSG
                        error_message += (
                            "**Possible causes:**\n"
                            "• Unsupported video codec or format\n"
                            "• Corrupted source file\n"
                            "• Incompatible video parameters\n"
                            "• Insufficient system resources\n\n"
                            "**Solutions:**\n"
                            "• Try downloading with a different quality\n"
                            "• Check if the source video is corrupted\n"
                            "• Try a different video source if available\n"
                            "• The original file will be sent without conversion\n\n"
                            f"**Technical details:** {error_details}"
                        )
                    else:
                        error_message = safe_get_messages(user_id).DOWN_UP_VIDEO_CONVERSION_FAILED_MSG
                        error_message += (
                            "**Solutions:**\n"
                            "• Try downloading with a different quality\n"
                            "• The original file will be sent without conversion\n"
                            "• If the problem persists, try a different video source\n\n"
                            f"**Technical details:** {error_details}"
                        )
                    
                    send_error_to_user(message, error_message)
                    logger.error(f"FFmpeg conversion failed: {error_details}")
                    break
                except Exception as e:
                    send_error_to_user(message, safe_get_messages(user_id).CONVERSION_TO_MP4_FAILED_MSG.format(error=e))
                    break

            after_rename_abs_path = os.path.abspath(user_vid_path)
            # --- YouTube thumbnail logic (priority over ffmpeg) ---
            youtube_thumb_path = None
            thumb_dir = None
            duration = 0
            thumb_source_url = video_page_url or url
            
            # Try to download YouTube thumbnail first
            if ("youtube.com" in thumb_source_url or "youtu.be" in thumb_source_url):
                try:
                    yt_id = video_id or None
                    if not yt_id:
                        try:
                            yt_id = extract_youtube_id(thumb_source_url, user_id)
                        except Exception:
                            yt_id = None
                    if yt_id:
                        youtube_thumb_path = os.path.join(dir_path, f"yt_thumb_{yt_id}.jpg")
                        download_thumbnail(yt_id, youtube_thumb_path, url)
                        if os.path.exists(youtube_thumb_path):
                            thumb_dir = youtube_thumb_path
                            logger.info(f"Downloaded thumbnail to download directory: {youtube_thumb_path}")
                            logger.info(f"Using YouTube thumbnail: {youtube_thumb_path}")
                except Exception as e:
                    logger.warning(f"YouTube thumbnail download failed: {e}")
            # If not YouTube or YouTube thumb not found, try universal thumbnail downloader
            if not thumb_dir:
                try:
                    universal_thumb_path = os.path.join(dir_path, "universal_thumb.jpg")
                    if download_universal_thumbnail(thumb_source_url, universal_thumb_path):
                        if os.path.exists(universal_thumb_path):
                            thumb_dir = universal_thumb_path
                            logger.info(f"Using universal thumbnail: {universal_thumb_path}")
                except Exception as e:
                    logger.info(f"Universal thumbnail not available: {e}")
            
            # Get video duration (always needed)
            try:
                width, height, duration = get_video_info_ffprobe(user_vid_path)
                if duration == 0:
                    logger.warning("Failed to get video duration via get_video_info_ffprobe, trying direct ffmpeg")
                    # Get FFmpeg path using the common function
                    from DOWN_AND_UP.ffmpeg import get_ffmpeg_path
                    ffmpeg_path = get_ffmpeg_path()
                    if not ffmpeg_path:
                        logger.error("ffmpeg not found in PATH or project directory.")
                        duration = 0
                    else:
                        ffmpeg_duration_command = [
                            ffmpeg_path, "-i", user_vid_path, "-f", "null", "-"
                        ]
                        result = subprocess.run(ffmpeg_duration_command, capture_output=True, text=True, timeout=int(30), encoding='utf-8', errors='replace')
                        output = result.stderr  # ffmpeg outputs info to stderr
                        duration_match = re.search(r'Duration: (\d{2}):(\d{2}):(\d{2})\.(\d{2})', output)
                        if duration_match:
                            hours, minutes, seconds, centiseconds = map(int, duration_match.groups())
                            duration = int(hours * 3600 + minutes * 60 + seconds + centiseconds / 100)
            except Exception as e:
                logger.warning(f"Failed to get video duration: {e}")
                duration = 0
            
            # Use ffmpeg thumbnail only as fallback (when both YouTube/universal thumbnails failed)
            if not thumb_dir:
                result = get_duration_thumb(message, dir_path, user_vid_path, sanitize_filename_strict(caption_name))
                if result is None:
                    logger.warning("Failed to create ffmpeg thumbnail fallback")
                    thumb_dir = None
                else:
                    duration_from_ffmpeg, thumb_dir_ffmpeg = result
                    thumb_dir = thumb_dir_ffmpeg
                    if duration == 0:  # Use duration from ffmpeg if we couldn't get it with ffprobe
                        duration = duration_from_ffmpeg
                    logger.info(f"Using ffmpeg thumbnail fallback: {thumb_dir}")
            
            # Check for the existence of a preview and create a default one if needed
            if thumb_dir and not os.path.exists(thumb_dir):
                logger.warning(f"Thumbnail not found at {thumb_dir}, creating default")
                create_default_thumbnail(os.path.join(dir_path, "default_thumb.jpg"))
                thumb_dir = os.path.join(dir_path, "default_thumb.jpg")
                if not os.path.exists(thumb_dir):
                    logger.warning("Failed to create default thumbnail, continuing without thumbnail")
                    thumb_dir = None

            video_size_in_bytes = os.path.getsize(user_vid_path)
            video_size = humanbytes(int(video_size_in_bytes))
            max_size = 1950 * 1024 * 1024  # 1.95 GB - close to Telegram's 2GB limit with 50MB safety margin
            if int(video_size_in_bytes) > max_size:
                safe_edit_message_text(user_id, proc_msg_id,
                    f"{info_text}\n{full_bar}   100.0%\n<i>⚠️ Your video size ({video_size}) is too large.</i>\n<i>Splitting file...</i> ✂️")
                returned = split_video_2(dir_path, sanitize_filename_strict(caption_name), after_rename_abs_path, int(video_size_in_bytes), max_size, int(duration), user_id, lazy=True)
                caption_lst = returned.get("video")
                path_lst = returned.get("path")
                ranges = list(returned.get("ranges") or [])
                failed_parts = []   # (part number, reason)
                part_title = original_video_title or caption_name
                part_base = sanitize_filename_strict(caption_name)
                # Note: split_msg_ids is already initialized at function start, don't reset it here
                for p in itertools.count():
                    if p >= len(caption_lst):
                        break
                    # cut this part now (not all parts up front): the disk holds the video + one part
                    reason = ""
                    while p < len(ranges):
                        if not os.path.exists(after_rename_abs_path):
                            reason = "source video file is gone"
                            break
                        ffmpeg_extract_subclip(after_rename_abs_path, ranges[p][0], ranges[p][1], targetname=path_lst[p])
                        size_p = os.path.getsize(path_lst[p]) if os.path.exists(path_lst[p]) else 0
                        if size_p > max_size and ranges[p][1] - ranges[p][0] > 60:
                            # uneven bitrate: this stretch is bigger than the rest -> cut it in two
                            os.remove(path_lst[p])
                            a, b = ranges[p]
                            ranges[p:p + 1] = [(a, (a + b) / 2), ((a + b) / 2, b)]
                            caption_lst = [f"{part_base} - Part {k + 1}" for k in range(len(ranges))]
                            path_lst = [os.path.join(dir_path, c + ".mp4") for c in caption_lst]
                            continue
                        reason = "" if size_p else "ffmpeg could not cut it (see app log)"
                        break
                    caption_name = caption_lst[p]
                    part_label = f"Part {p + 1}/{len(caption_lst)}"
                    if p == len(caption_lst) - 1 and os.path.exists(after_rename_abs_path):
                        os.remove(after_rename_abs_path)  # every part is cut: free the space for the upload
                    if not reason and os.path.getsize(path_lst[p]) > 2000 * 1024 * 1024:
                        reason = "still over 2 GB after cutting"
                    if reason:
                        logger.error(f"split {part_label} failed: {reason}")
                        failed_parts.append((p + 1, reason))
                        if os.path.exists(path_lst[p]):
                            os.remove(path_lst[p])
                        continue
                    part_result = get_duration_thumb(message, dir_path, path_lst[p], sanitize_filename_strict(caption_name))
                    if part_result is None:
                        failed_parts.append((p + 1, "ffprobe could not read the part"))
                        os.remove(path_lst[p])
                        continue
                    part_duration, splited_thumb_dir = part_result
                    # --- TikTok: Don't Pass Title ---
                    part_err = None
                    try:  # one failed part must not stop the next ones
                        video_msg = send_videos(message, path_lst[p], '' if force_no_title else part_title, part_duration, splited_thumb_dir, info_text, proc_msg.id, full_video_title, tags_text_final, part=part_label)
                    except Exception as e_part:
                        if "Cancelled" in type(e_part).__name__:
                            raise
                        logger.error(f"split part {part_label} upload failed: {e_part}")
                        part_err, video_msg = e_part, None
                    if not video_msg:
                        logger.error("send_videos returned None for split part; skipping cache save for this part")
                        failed_parts.append((p + 1, f"upload failed: {part_err}" if part_err else "upload failed"))
                        if os.path.exists(path_lst[p]):
                            os.remove(path_lst[p])
                        continue
                    #found_type = None
                    # Note: Forwarding to log channels is now handled in send_videos function
                    # We need to get the forwarded message IDs from the log channel for caching
                    try:
                        # Determine the correct log channel based on content type
                        from HELPERS.porn import is_porn
                        is_nsfw = is_porn(url, "", "", None) or user_forced_nsfw
                        logger.info(f"[FALLBACK] is_porn check for {url}: {is_porn(url, '', '', None)}, user_forced_nsfw: {user_forced_nsfw}, final is_nsfw: {is_nsfw}")
                        is_private_chat = getattr(message.chat, "type", None) == enums.ChatType.PRIVATE
                        is_paid = is_nsfw and is_private_chat and LimitsConfig.NSFW_PAID
                        logger.info(f"[VIDEO CACHE] URL analysis: url={url}, is_nsfw={is_nsfw}, is_private_chat={is_private_chat}, is_paid={is_paid}")
                        
                        # Handle different content types according to new logic
                        if is_paid:
                            # For NSFW content in private chat, send_videos already sent paid media to user
                            # Send paid copy to LOGS_PAID_ID and open copy to LOGS_NSFW_ID for history
                            
                            # Send paid copy to LOGS_PAID_ID
                            log_channel_paid = get_log_channel("video", paid=True)
                            try:
                                # Forward the paid video to LOGS_PAID_ID
                                safe_forward_messages(log_channel_paid, user_id, [video_msg.id])
                                logger.info(f"down_and_up: NSFW content paid copy sent to PAID channel")
                            except Exception as e:
                                logger.error(f"down_and_up: failed to send paid copy to PAID channel: {e}")
                            
                            # Send open copy to LOGS_NSFW_ID for history
                            log_channel_nsfw = get_log_channel("video", nsfw=True)
                            if log_channel_nsfw and log_channel_nsfw != 0:
                                try:
                                    # Get video dimensions for proper aspect ratio
                                    try:
                                        v_w, v_h, v_dur = get_video_info_ffprobe(path_lst[p])
                                    except Exception:
                                        v_w, v_h, v_dur = width, height, part_duration
                                    
                                    # Create open copy for history (without stars) - send directly to NSFW channel
                                    open_video_msg = app.send_video(
                                        chat_id=log_channel_nsfw,
                                        video=path_lst[p],
                                        caption=caption_lst[p] if caption_lst and p < len(caption_lst) else f"part_{p+1}",
                                        duration=int(v_dur) if v_dur else part_duration,
                                        width=int(v_w) if v_w else width,
                                        height=int(v_h) if v_h else height,
                                        thumb=splited_thumb_dir,
                                        reply_parameters=ReplyParameters(message_id=message.id)
                                    )
                                    logger.info(f"down_and_up: NSFW content open copy sent to NSFW channel for history")
                                    already_forwarded_to_log = True
                                except Exception as e:
                                    logger.error(f"down_and_up: failed to send open copy to NSFW channel: {e}")
                            else:
                                logger.warning(f"down_and_up: NSFW channel not available (ID: {log_channel_nsfw}), skipping open copy")
                            
                            # Don't cache NSFW content
                            logger.info(f"down_and_up: NSFW content sent to user (paid), PAID channel (paid copy), and NSFW channel (open copy), not cached")
                            forwarded_msgs = None
                            
                        elif is_nsfw:
                            # NSFW content in groups -> LOGS_NSFW_ID only
                            # For split videos, always forward each part to NSFW channel
                            # For playlists, always forward each video to NSFW channel (don't use already_forwarded_to_log)
                            # IMPORTANT: For split videos in playlists, only forward once (split video takes priority)
                            if caption_lst and len(caption_lst) > 1:
                                # This is a split video - always forward each part (even if it's in a playlist)
                                log_channel = get_log_channel("video", nsfw=True)
                                if log_channel and log_channel != 0:
                                    try:
                                        forwarded_msgs = safe_forward_messages(log_channel, user_id, [video_msg.id])
                                        logger.info(f"down_and_up: NSFW content sent to NSFW channel")
                                    except Exception as e:
                                        logger.error(f"down_and_up: failed to forward to NSFW channel: {e}")
                                        forwarded_msgs = None
                                else:
                                    logger.warning(f"down_and_up: NSFW channel not available (ID: {log_channel}), skipping forward")
                                    forwarded_msgs = None
                            elif is_playlist:
                                # For playlists (non-split videos), always forward each video to NSFW channel
                                log_channel = get_log_channel("video", nsfw=True)
                                if log_channel and log_channel != 0:
                                    try:
                                        forwarded_msgs = safe_forward_messages(log_channel, user_id, [video_msg.id])
                                        logger.info(f"down_and_up: NSFW content sent to NSFW channel")
                                    except Exception as e:
                                        logger.error(f"down_and_up: failed to forward to NSFW channel: {e}")
                                        forwarded_msgs = None
                                else:
                                    logger.warning(f"down_and_up: NSFW channel not available (ID: {log_channel}), skipping forward")
                                    forwarded_msgs = None
                            elif not already_forwarded_to_log:
                                already_forwarded_to_log = True  # Set flag BEFORE forward to prevent duplicates
                                log_channel = get_log_channel("video", nsfw=True)
                                if log_channel and log_channel != 0:
                                    try:
                                        forwarded_msgs = safe_forward_messages(log_channel, user_id, [video_msg.id])
                                        logger.info(f"down_and_up: NSFW content sent to NSFW channel")
                                    except Exception as e:
                                        logger.error(f"down_and_up: failed to forward to NSFW channel: {e}")
                                        forwarded_msgs = None
                                else:
                                    logger.warning(f"down_and_up: NSFW channel not available (ID: {log_channel}), skipping forward")
                                    forwarded_msgs = None
                            else:
                                logger.info("down_and_up: skipping forward to NSFW channel - already forwarded to log")
                                forwarded_msgs = None
                            
                            # Don't cache NSFW content
                            logger.info(f"down_and_up: NSFW content sent to NSFW channel, not cached")
                            
                        else:
                            # Regular content -> LOGS_VIDEO_ID and cache
                            # For split videos, always forward each part to log channel
                            # For playlists, always forward each video to log channel (don't use already_forwarded_to_log)
                            # IMPORTANT: For split videos in playlists, only forward once (split video takes priority)
                            if caption_lst and len(caption_lst) > 1:
                                # This is a split video - always forward each part (even if it's in a playlist)
                                log_channel = get_log_channel("video")
                                forwarded_msgs = safe_forward_messages(log_channel, user_id, [video_msg.id])
                            elif is_playlist:
                                # For playlists (non-split videos), always forward each video to log channel
                                log_channel = get_log_channel("video")
                                forwarded_msgs = safe_forward_messages(log_channel, user_id, [video_msg.id])
                            elif not already_forwarded_to_log:
                                already_forwarded_to_log = True  # Set flag BEFORE forward to prevent duplicates
                                log_channel = get_log_channel("video")
                                forwarded_msgs = safe_forward_messages(log_channel, user_id, [video_msg.id])
                            else:
                                logger.info("down_and_up: skipping forward to LOGS_VIDEO_ID - already forwarded to log")
                                forwarded_msgs = None
                        logger.info(f"down_and_up: forwarded_msgs result: {forwarded_msgs}")
                    except Exception as e:
                        # Check if error is related to quality_key - if so, ignore it completely
                        if "'quality_key'" in str(e):
                            logger.info(f"quality_key error ignored (non-critical): {e}")
                            # Quality_key errors don't affect functionality, just continue
                        else:
                            logger.error(f"Error forwarding video to logger: {e}")
                        logger.info(f"down_and_up: collecting video_msg.id after error for split video: {video_msg.id}")
                        
                        # PREVENTIVE FIX: Handle split video completion even after quality_key error
                        if split_msg_ids and not is_playlist:
                            logger.info(f"PREVENTIVE FIX: Processing split video completion after quality_key error in loop: {split_msg_ids}")
                            actual_video_count = len(split_msg_ids)
                            success_msg = f"<b>{safe_get_messages(user_id).DOWN_UP_UPLOAD_COMPLETE_MSG}</b> - {actual_video_count} {safe_get_messages(user_id).DOWN_UP_FILES_UPLOADED_MSG}.\n{safe_get_messages(user_id).CREDITS_MSG}"
                            logger.info(f"PREVENTIVE FIX: sending final success message for split video: {success_msg}")
                            safe_delete_messages(chat_id=user_id, message_ids=[proc_msg_id], revoke=True)  # file itself is the success signal
                            send_to_logger(message, safe_get_messages(user_id).VIDEO_UPLOAD_COMPLETED_SPLITTING_LOG_MSG)
                            break
                    if caption_lst and p < len(caption_lst) - 1:
                        pass
                    if os.path.exists(splited_thumb_dir):
                        os.remove(splited_thumb_dir)
                    if os.path.exists(path_lst[p]):
                        os.remove(path_lst[p])
                
                # Save all parts of split video to cache after the loop is completed
                logger.info(f"down_and_up: checking split_msg_ids for cache save: {split_msg_ids}, is_playlist={is_playlist}")
                if split_msg_ids and not is_playlist:
                    # Remove duplicates
                    split_msg_ids = list(dict.fromkeys(split_msg_ids))
                    logger.info(f"down_and_up: saving all split video parts to cache: {split_msg_ids}")
                    
                    # Update safe_quality_key to the actual quality used for splitting
                    if quality_key and quality_key != "best":
                        safe_quality_key = quality_key
                        logger.info(f"down_and_up: updated safe_quality_key for split video: {safe_quality_key}")
                    
                    _save_video_cache_with_logging(url, safe_quality_key, split_msg_ids, original_text=message.text or message.caption or "", user_id=user_id)
                else:
                    logger.warning(f"down_and_up: NOT saving to cache - split_msg_ids={split_msg_ids}, is_playlist={is_playlist}")
                if os.path.exists(thumb_dir):
                    os.remove(thumb_dir)
                if os.path.exists(user_vid_path):
                    os.remove(user_vid_path)
                # Use the actual number of split parts for the success message
                actual_video_count = len(split_msg_ids) if split_msg_ids else video_count
                success_msg = f"<b>{safe_get_messages(user_id).DOWN_UP_UPLOAD_COMPLETE_MSG}</b> - {actual_video_count} {safe_get_messages(user_id).DOWN_UP_FILES_UPLOADED_MSG}.\n{safe_get_messages(user_id).CREDITS_MSG}"
                logger.info(f"down_and_up: sending final success message for split video: {success_msg}")
                if failed_parts:  # say which parts are missing instead of failing silently
                    sent_n = len(caption_lst) - len(failed_parts)
                    safe_edit_message_text(user_id, proc_msg_id,
                        f"⚠️ <b>Sent {sent_n} of {len(caption_lst)} parts.</b>\n"
                        f"<blockquote>Part {', '.join(str(n) for n, _ in failed_parts)} could not be sent. Send the link again to retry.</blockquote>",
                        parse_mode=enums.ParseMode.HTML)
                    import shutil as _shutil_split
                    free_gb = _shutil_split.disk_usage(dir_path).free / 1024 ** 3
                    log_error_to_channel(message, "Split video: " + "; ".join(f"part {n}/{len(caption_lst)}: {why}" for n, why in failed_parts)
                                         + f" · free disk {free_gb:.1f} GB", url)
                else:
                    safe_delete_messages(chat_id=user_id, message_ids=[proc_msg_id], revoke=True)  # file itself is the success signal
                send_to_logger(message, safe_get_messages(user_id).VIDEO_UPLOAD_COMPLETED_SPLITTING_LOG_MSG)
                
            else:
                if final_name:
                    # Read the full name from the file
                    full_caption = caption_name
                    try:
                        if os.path.exists(full_title_path):
                            with open(full_title_path, "r", encoding="utf-8") as f:
                                full_caption = f.read().strip()
                    except Exception as e:
                        logger.error(f"Error reading full title: {e}")

                    # Check for preview existence before sending
                    if thumb_dir and not os.path.exists(thumb_dir):
                        logger.warning(f"Thumbnail not found before sending, creating default")
                        create_default_thumbnail(os.path.join(dir_path, "default_thumb.jpg"))
                        thumb_dir = os.path.join(dir_path, "default_thumb.jpg")
                        if not os.path.exists(thumb_dir):
                            logger.warning("Failed to create default thumbnail before sending, continuing without thumbnail")
                            thumb_dir = None

                    try:
                        # --- TikTok: Don't Pass Title ---
                        # Embed subtitles if needed (only for single videos, not playlists)
                        is_playlist_mode = video_count > 1 or is_playlist_with_range(original_text)
                        video_msg = send_videos(message, after_rename_abs_path, '' if force_no_title else original_video_title, duration, thumb_dir, info_text, proc_msg.id, full_video_title, tags_text_final)
                        if not video_msg:
                            logger.error("send_videos returned None for single video; aborting cache save for this item")
                            continue
                        
                        # Save video message ID for caching purposes
                        last_video_msg_id = video_msg.id
                        
                        #found_type = None
                        try:
                            # Determine the correct log channel based on content type
                            from HELPERS.porn import is_porn
                            is_nsfw = is_porn(url, "", "", None) or user_forced_nsfw
                            logger.info(f"[FALLBACK] is_porn check for {url}: {is_porn(url, '', '', None)}, user_forced_nsfw: {user_forced_nsfw}, final is_nsfw: {is_nsfw}")
                            is_private_chat = getattr(message.chat, "type", None) == enums.ChatType.PRIVATE
                            # Detect if actually sent as paid media
                            try:
                                msg_is_paid = (
                                    getattr(video_msg, "media", None) == enums.MessageMediaType.PAID_MEDIA
                                ) or (getattr(video_msg, "paid_media", None) is not None)
                            except Exception:
                                msg_is_paid = False
                            is_paid = msg_is_paid or (is_nsfw and is_private_chat and LimitsConfig.NSFW_PAID)
                            
                            # Handle different content types according to new logic
                            if is_paid:
                                # For NSFW content in private chat, send_videos already sent paid media to user
                                # Send paid copy to LOGS_PAID_ID and open copy to LOGS_NSFW_ID for history
                                
                                # Send paid copy to LOGS_PAID_ID
                                log_channel_paid = get_log_channel("video", paid=True)
                                try:
                                    # Forward the paid video to LOGS_PAID_ID
                                    safe_forward_messages(log_channel_paid, user_id, [video_msg.id])
                                    logger.info(f"down_and_up: NSFW content paid copy sent to PAID channel")
                                except Exception as e:
                                    logger.error(f"down_and_up: failed to send paid copy to PAID channel: {e}")
                                
                                # Send open copy to LOGS_NSFW_ID for history
                                log_channel_nsfw = get_log_channel("video", nsfw=True)
                                try:
                                    # Get video dimensions for proper aspect ratio
                                    try:
                                        v_w, v_h, v_dur = get_video_info_ffprobe(after_rename_abs_path)
                                    except Exception:
                                        v_w, v_h, v_dur = width, height, duration
                                    
                                    # Create open copy for history (without stars) - send directly to NSFW channel
                                    open_video_msg = app.send_video(
                                        chat_id=log_channel_nsfw,
                                        video=after_rename_abs_path,
                                        caption='' if force_no_title else original_video_title,
                                        duration=int(v_dur) if v_dur else duration,
                                        width=int(v_w) if v_w else width,
                                        height=int(v_h) if v_h else height,
                                        thumb=thumb_dir,
                                        reply_parameters=ReplyParameters(message_id=message.id)
                                    )
                                    logger.info(f"down_and_up: NSFW content open copy sent to NSFW channel for history")
                                    already_forwarded_to_log = True
                                except Exception as e:
                                    logger.error(f"down_and_up: failed to send open copy to NSFW channel: {e}")
                                
                                # Don't cache NSFW content
                                logger.info(f"down_and_up: NSFW content sent to user (paid), PAID channel (paid copy), and NSFW channel (open copy), not cached")
                                forwarded_msgs = None
                                
                            elif is_nsfw:
                                # NSFW content in groups -> LOGS_NSFW_ID only
                                # For split videos, always forward each part to NSFW channel
                                # For playlists, always forward each video to NSFW channel (don't use already_forwarded_to_log)
                                # IMPORTANT: For split videos in playlists, only forward once (split video takes priority)
                                if caption_lst and len(caption_lst) > 1:
                                    # This is a split video - always forward each part (even if it's in a playlist)
                                    log_channel = get_log_channel("video", nsfw=True)
                                    forwarded_msgs = safe_forward_messages(log_channel, user_id, [video_msg.id])
                                elif is_playlist:
                                    # For playlists (non-split videos), always forward each video to NSFW channel
                                    log_channel = get_log_channel("video", nsfw=True)
                                    forwarded_msgs = safe_forward_messages(log_channel, user_id, [video_msg.id])
                                elif not already_forwarded_to_log:
                                    already_forwarded_to_log = True  # Set flag BEFORE forward to prevent duplicates
                                    log_channel = get_log_channel("video", nsfw=True)
                                    forwarded_msgs = safe_forward_messages(log_channel, user_id, [video_msg.id])
                                else:
                                    logger.info("down_and_up: skipping forward to NSFW channel - already forwarded to log")
                                    forwarded_msgs = None
                                # Don't cache NSFW content
                                logger.info(f"down_and_up: NSFW content sent to NSFW channel, not cached")
                            else:
                                # Regular content -> LOGS_VIDEO_ID and cache (but never for paid media)
                                if (
                                    getattr(video_msg, "media", None) == enums.MessageMediaType.PAID_MEDIA
                                ) or (getattr(video_msg, "paid_media", None) is not None):
                                    logger.info("down_and_up: skipping forward to LOGS_VIDEO_ID for paid media")
                                    forwarded_msgs = None
                                elif caption_lst and len(caption_lst) > 1:
                                    # This is a split video - always forward each part
                                    log_channel = get_log_channel("video")
                                    forwarded_msgs = safe_forward_messages(log_channel, user_id, [video_msg.id])
                                elif is_playlist:
                                    # For playlists, always forward each video to log channel (don't use already_forwarded_to_log)
                                    log_channel = get_log_channel("video")
                                    forwarded_msgs = safe_forward_messages(log_channel, user_id, [video_msg.id])
                                elif not already_forwarded_to_log:
                                    log_channel = get_log_channel("video")
                                    forwarded_msgs = safe_forward_messages(log_channel, user_id, [video_msg.id])
                                else:
                                    logger.info("down_and_up: skipping forward to LOGS_VIDEO_ID - already forwarded to log")
                                    forwarded_msgs = None
                            logger.info(f"down_and_up: forwarded_msgs result: {forwarded_msgs}")
                            if forwarded_msgs:
                                logger.info(f"down_and_up: saving to cache with forwarded message IDs: {[m.id for m in forwarded_msgs]}")
                            else:
                                # If forwarding failed, try to forward manually and get log channel IDs
                                # For playlists, skip manual forward if we already tried to forward (to avoid duplicates)
                                if is_playlist:
                                    logger.info("down_and_up: forwarding failed for playlist video, but skipping manual forward to avoid duplicates")
                                elif 'already_forwarded_to_log' in locals() and already_forwarded_to_log:
                                    logger.info("down_and_up: already forwarded to log; skipping manual forward duplicate")
                                else:
                                    logger.info(f"down_and_up: forwarding failed, trying manual forward for video: {video_msg.id}")
                                    try:
                                        # Determine the correct log channel based on content type
                                        from HELPERS.porn import is_porn
                                        is_nsfw = is_porn(url, "", "", None) or user_forced_nsfw
                                        logger.info(f"[FALLBACK] is_porn check for {url}: {is_porn(url, '', '', None)}, user_forced_nsfw: {user_forced_nsfw}, final is_nsfw: {is_nsfw}")
                                        is_private_chat = getattr(message.chat, "type", None) == enums.ChatType.PRIVATE
                                        try:
                                            msg_is_paid = (
                                                getattr(video_msg, "media", None) == enums.MessageMediaType.PAID_MEDIA
                                            ) or (getattr(video_msg, "paid_media", None) is not None)
                                        except Exception:
                                            msg_is_paid = False
                                        is_paid = msg_is_paid or (is_nsfw and is_private_chat and LimitsConfig.NSFW_PAID)
                                        
                                        # Handle different content types according to new logic
                                        if is_paid:
                                            # For NSFW content in private chat, send to both channels but don't cache
                                            # For NSFW content in private chat, send_videos already sent paid media to user
                                            # No need to forward to LOGS_PAID_ID as it's already sent
                                            
                                            # Send to LOGS_NSFW_ID (for history) - send open copy, not paid media
                                            # LOGS_PAID_ID and LOGS_NSWF_ID were already handled in the main logic above
                                            # No need to send again in manual forward
                                            
                                            # Don't cache NSFW content
                                            logger.info(f"down_and_up: NSFW content already sent to user (paid), PAID channel (paid copy), and NSFW channel (open copy), not cached (manual)")
                                            forwarded_msgs = None
                                            
                                        elif is_nsfw:
                                            # NSFW content in groups -> LOGS_NSFW_ID only
                                            log_channel = get_log_channel("video", nsfw=True)
                                            try:
                                                safe_forward_messages(log_channel, user_id, [video_msg.id])
                                                logger.info(f"down_and_up: NSFW content sent to NSFW channel (manual)")
                                            except Exception as e:
                                                logger.error(f"down_and_up: failed to forward to NSFW channel (manual): {e}")
                                            
                                            # Don't cache NSFW content
                                            logger.info(f"down_and_up: NSFW content sent to NSFW channel, not cached (manual)")
                                            forwarded_msgs = None
                                            
                                        else:
                                            # Regular content -> LOGS_VIDEO_ID and cache (but never for paid media)
                                            if (
                                                getattr(video_msg, "media", None) == enums.MessageMediaType.PAID_MEDIA
                                            ) or (getattr(video_msg, "paid_media", None) is not None):
                                                logger.info("down_and_up: skipping forward to LOGS_VIDEO_ID for paid media (manual)")
                                                forwarded_msgs = None
                                            elif caption_lst and len(caption_lst) > 1:
                                                # This is a split video - always forward each part
                                                log_channel = get_log_channel("video")
                                                forwarded_msgs = safe_forward_messages(log_channel, user_id, [video_msg.id])
                                            elif not already_forwarded_to_log:
                                                log_channel = get_log_channel("video")
                                                forwarded_msgs = safe_forward_messages(log_channel, user_id, [video_msg.id])
                                            else:
                                                logger.info("down_and_up: skipping forward to LOGS_VIDEO_ID - already forwarded to log (manual)")
                                                forwarded_msgs = None
                                    except Exception as e:
                                        logger.error(f"Error in manual forward: {e}")
                        except Exception as e:
                            # Check if error is related to quality_key - if so, ignore it completely
                            if "'quality_key'" in str(e):
                                logger.info(f"quality_key error ignored (non-critical): {e}")
                                # Quality_key errors don't affect functionality, just continue
                                
                                # PREVENTIVE FIX: Handle split video completion even after quality_key error
                                if split_msg_ids and not is_playlist:
                                    logger.info(f"PREVENTIVE FIX: Processing split video completion after quality_key error in manual forward: {split_msg_ids}")
                                    actual_video_count = len(split_msg_ids)
                                    success_msg = f"<b>{safe_get_messages(user_id).DOWN_UP_UPLOAD_COMPLETE_MSG}</b> - {actual_video_count} {safe_get_messages(user_id).DOWN_UP_FILES_UPLOADED_MSG}.\n{safe_get_messages(user_id).CREDITS_MSG}"
                                    logger.info(f"PREVENTIVE FIX: sending final success message for split video: {success_msg}")
                                    safe_delete_messages(chat_id=user_id, message_ids=[proc_msg_id], revoke=True)  # file itself is the success signal
                                    send_to_logger(message, safe_get_messages(user_id).VIDEO_UPLOAD_COMPLETED_SPLITTING_LOG_MSG)
                
                            else:
                                logger.error(f"Error forwarding video to logger: {e}")
                            # Try to forward manually even after error
                            try:
                                # Safe quality_key for error recovery (already defined at function start)
                                
                                # Determine the correct log channel based on content type
                                from HELPERS.porn import is_porn
                                is_nsfw = is_porn(url, "", "", None) or user_forced_nsfw
                                logger.info(f"[FALLBACK] is_porn check for {url}: {is_porn(url, '', '', None)}, user_forced_nsfw: {user_forced_nsfw}, final is_nsfw: {is_nsfw}")
                                is_private_chat = getattr(message.chat, "type", None) == enums.ChatType.PRIVATE
                                is_paid = is_nsfw and is_private_chat and LimitsConfig.NSFW_PAID
                                
                                # Handle different content types according to new logic
                                if is_paid:
                                    # For NSFW content in private chat, send_videos already sent paid media to user
                                    # Send paid copy to LOGS_PAID_ID and open copy to LOGS_NSFW_ID for history
                                    
                                    # Send paid copy to LOGS_PAID_ID
                                    log_channel_paid = get_log_channel("video", paid=True)
                                    try:
                                        # Forward the paid video to LOGS_PAID_ID
                                        safe_forward_messages(log_channel_paid, user_id, [video_msg.id])
                                        logger.info(f"down_and_up: NSFW content paid copy sent to PAID channel (error recovery)")
                                    except Exception as e:
                                        logger.error(f"down_and_up: failed to send paid copy to PAID channel (error recovery): {e}")
                                    
                                    # Send open copy to LOGS_NSFW_ID for history
                                    log_channel_nsfw = get_log_channel("video", nsfw=True)
                                    try:
                                        # Get video dimensions for proper aspect ratio
                                        try:
                                            v_w, v_h, v_dur = get_video_info_ffprobe(after_rename_abs_path)
                                        except Exception:
                                            v_w, v_h, v_dur = width, height, duration
                                        
                                        # Create open copy for history (without stars) - send directly to NSFW channel
                                        open_video_msg = app.send_video(
                                            chat_id=log_channel_nsfw,
                                            video=after_rename_abs_path,
                                            caption='' if force_no_title else original_video_title,
                                            duration=int(v_dur) if v_dur else duration,
                                            width=int(v_w) if v_w else width,
                                            height=int(v_h) if v_h else height,
                                            thumb=thumb_dir,
                                            reply_parameters=ReplyParameters(message_id=message.id)
                                        )
                                        logger.info(f"down_and_up: NSFW content open copy sent to NSFW channel for history (error recovery)")
                                        already_forwarded_to_log = True
                                    except Exception as e:
                                        logger.error(f"down_and_up: failed to send open copy to NSFW channel (error recovery): {e}")
                                    
                                    # Don't cache NSFW content
                                    logger.info(f"down_and_up: NSFW content sent to user (paid), PAID channel (paid copy), and NSFW channel (open copy), not cached (error recovery)")
                                    forwarded_msgs = None
                                    
                                elif is_nsfw:
                                    # NSFW content in groups -> LOGS_NSFW_ID only
                                    log_channel = get_log_channel("video", nsfw=True)
                                    try:
                                        safe_forward_messages(log_channel, user_id, [video_msg.id])
                                        logger.info(f"down_and_up: NSFW content sent to NSFW channel (error recovery)")
                                    except Exception as e:
                                        logger.error(f"down_and_up: failed to forward to NSFW channel (error recovery): {e}")
                                    
                                    # Don't cache NSFW content
                                    logger.info(f"down_and_up: NSFW content sent to NSFW channel, not cached (error recovery)")
                                    forwarded_msgs = None
                                    
                                else:
                                    # Regular content -> LOGS_VIDEO_ID and cache
                                    if caption_lst and len(caption_lst) > 1:
                                        # This is a split video - always forward each part
                                        log_channel = get_log_channel("video")
                                        forwarded_msgs = safe_forward_messages(log_channel, user_id, [video_msg.id])
                                    elif not already_forwarded_to_log:
                                        log_channel = get_log_channel("video")
                                        forwarded_msgs = safe_forward_messages(log_channel, user_id, [video_msg.id])
                                    else:
                                        logger.info("down_and_up: skipping forward to LOGS_VIDEO_ID - already forwarded to log (error recovery)")
                                        forwarded_msgs = None
                            except Exception as e2:
                                # Check if error is related to quality_key - if so, ignore it completely
                                if "'quality_key'" in str(e2):
                                    logger.info(f"quality_key error ignored (non-critical): {e2}")
                                    # Quality_key errors don't affect functionality, just continue
                                    
                                    # PREVENTIVE FIX: Handle split video completion even after quality_key error
                                    if split_msg_ids and not is_playlist:
                                        logger.info(f"PREVENTIVE FIX: Processing split video completion after quality_key error in manual forward after error: {split_msg_ids}")
                                        actual_video_count = len(split_msg_ids)
                                        success_msg = f"<b>{safe_get_messages(user_id).DOWN_UP_UPLOAD_COMPLETE_MSG}</b> - {actual_video_count} {safe_get_messages(user_id).DOWN_UP_FILES_UPLOADED_MSG}.\n{safe_get_messages(user_id).CREDITS_MSG}"
                                        logger.info(f"PREVENTIVE FIX: sending final success message for split video: {success_msg}")
                                        safe_delete_messages(chat_id=user_id, message_ids=[proc_msg_id], revoke=True)  # file itself is the success signal
                                        send_to_logger(message, safe_get_messages(user_id).VIDEO_UPLOAD_COMPLETED_SPLITTING_LOG_MSG)
                # end-of-task subs cache clearing handled in unified success branches below
                                else:
                                    logger.error(f"Error in manual forward after error: {e2}")
                        if is_playlist:  # the card is used again for the next video
                            safe_edit_message_text(user_id, proc_msg_id,
                                f"{info_text}\n{full_bar}   100.0%\n<b>{safe_get_messages(user_id).DOWN_UP_VIDEO_DURATION_MSG}</b> <i>{TimeFormatter(duration * 1000)}</i>\n{safe_get_messages(user_id).DOWN_UP_ONE_FILE_UPLOADED_MSG}")
                        else:
                            safe_delete_messages(chat_id=user_id, message_ids=[proc_msg_id], revoke=True)  # file itself is the success signal
                        
                        # Clean up video file and thumbnail
                        if os.path.exists(after_rename_abs_path):
                            os.remove(after_rename_abs_path)
                        if thumb_dir and os.path.exists(thumb_dir):
                            os.remove(thumb_dir)
                    except Exception as e:
                        logger.error(f"Error sending video: {e}")
                        logger.error(traceback.format_exc())
                        send_error_to_user(message, safe_get_messages(user_id).ERROR_SENDING_VIDEO_MSG.format(error=str(e)))
                        continue

        if is_playlist and safe_quality_key:
            total_sent = len(cached_videos) + successful_uploads
            app.send_message(user_id, safe_get_messages(user_id).PLAYLIST_VIDEOS_SENT_MSG.format(sent=total_sent, total=len(requested_indices)), reply_parameters=ReplyParameters(message_id=message.id))
            send_to_logger(message, safe_get_messages(user_id).PLAYLIST_VIDEOS_SENT_LOG_MSG.format(sent=total_sent, total=len(requested_indices), quality=safe_quality_key, user_id=user_id))

    except Exception as e:
        
        # Immediate cleanup of temporary status messages on error
        try:
            if status_msg_id:
                safe_delete_messages(chat_id=user_id, message_ids=[status_msg_id], revoke=True)
            if hourglass_msg_id:
                safe_delete_messages(chat_id=user_id, message_ids=[hourglass_msg_id], revoke=True)
            if download_started_msg_id:
                safe_delete_messages(chat_id=user_id, message_ids=[download_started_msg_id], revoke=True)
            stop_anim.set()
        except Exception:
            pass
        
        # Clean up temporary files on error
        try:
            cleanup_user_temp_files(user_id)
        except Exception as cleanup_error:
            logger.error(f"Error cleaning up temp files after error for user {user_id}: {cleanup_error}")
    finally:
        set_active_download(user_id, False)
        clear_download_start_time(user_id)  # Clear the download start time
        if playlist_name:
            with playlist_errors_lock:
                error_key = f"{user_id}_{playlist_name}"
                if error_key in playlist_errors:
                    del playlist_errors[error_key]

        # Clean up temporary files
        try:
            cleanup_user_temp_files(user_id)
        except Exception as e:
            logger.error(f"Error cleaning up temp files for user {user_id}: {e}")

        try:
            if status_msg_id:
                safe_delete_messages(chat_id=user_id, message_ids=[status_msg_id], revoke=True)
            if hourglass_msg_id:
                safe_delete_messages(chat_id=user_id, message_ids=[hourglass_msg_id], revoke=True)
        except Exception as e:
            logger.error(f"Error deleting status messages: {e}")
        # Also try to delete the 'Download started' message if it still exists
        try:
            if download_started_msg_id:
                safe_delete_messages(chat_id=user_id, message_ids=[download_started_msg_id], revoke=True)
        except Exception:
            pass

        # --- ADDED: summary of cache after cycle ---

#########################################
