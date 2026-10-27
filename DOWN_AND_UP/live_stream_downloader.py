# Live Stream Downloader
# Downloads live streams in chunks and sends them immediately

import os
import yt_dlp
import time
from datetime import datetime
from CONFIG.limits import LimitsConfig
from CONFIG.messages import safe_get_messages
from HELPERS.logger import logger
from DOWN_AND_UP.sender import send_videos
from DOWN_AND_UP.ffmpeg import get_duration_thumb, get_video_info_ffprobe


def download_live_stream_chunked(
    app, message, url, user_id, user_dir_name, info_dict, 
    proc_msg_id, current_total_process, tags_text="", 
    cookies_already_checked=False, use_proxy=False,
    format_override=None, quality_key=None
):
    """
    Download live stream in chunks and send each chunk immediately.
    
    Args:
        app: Pyrogram app instance
        message: Telegram message object
        url: Live stream URL
        user_id: User ID
        user_dir_name: Directory for downloads
        info_dict: Video info dict from yt-dlp
        proc_msg_id: Progress message ID
        current_total_process: Progress text
        tags_text: Tags text
        cookies_already_checked: Whether cookies were already checked
        use_proxy: Whether to use proxy
        
    Returns:
        bool: True if successful, False otherwise
    """

