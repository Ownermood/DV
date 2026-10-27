from HELPERS.app_instance import get_app
from HELPERS.logger import send_to_user, send_to_logger, send_to_all, send_error_to_user
from pyrogram import filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from HELPERS.safe_messeger import safe_send_message, safe_send_message_with_auto_delete, safe_edit_message_text, safe_delete_messages, fake_message
from CONFIG.config import Config
from CONFIG.messages import Messages, safe_get_messages
from datetime import datetime
import subprocess
import sys
import math
import time
import os
import re
from typing import Optional
import threading
# from DATABASE.cache_db import reload_firebase_cache, get_from_local_cache  # moved to lazy imports
from DATABASE.firebase_init import db
from URL_PARSERS.youtube import is_youtube_url, youtube_to_short_url, youtube_to_long_url
from URL_PARSERS.normalizer import normalize_url_for_cache, get_clean_playlist_url
from HELPERS.limitter import TimeFormatter, is_user_in_channel
# Channel guard helpers
from HELPERS.channel_guard import (
    get_channel_guard,
    parse_period_to_seconds,
    format_seconds_human,
    register_block_user_executor,
)
# from DATABASE.cache_db import get_url_hash, db_child_by_path  # moved to lazy imports
from HELPERS.logger import logger
from HELPERS.decorators import background_handler

# Global variable for bot start time
starting_point = [time.time()]

# Get app instance for decorators
app = get_app()

@app.on_message(filters.command("reload_cache") & filters.private)
@background_handler(label="reload_cache")
def reload_firebase_cache_command(app, message):
    messages = safe_get_messages(message.chat.id)
    """The processor of command for rebooting the local cache Firebase"""
    if int(message.chat.id) not in Config.ADMIN:
        safe_send_message_with_auto_delete(message.chat.id, safe_get_messages(message.chat.id).ADMIN_ACCESS_DENIED_AUTO_DELETE_MSG, delete_after_seconds=60)
        return
    
    # Check if this is a fake message (called programmatically)
    is_fake_message = getattr(message, '_is_fake_message', False) or message.id == 0
    
    # In local mode, reload cache from file
    use_firebase = getattr(Config, 'USE_FIREBASE', True)
    if not use_firebase:
        from DATABASE.cache_db import reload_firebase_cache
        success = reload_firebase_cache()
        if success:
            final_msg = "✅ Local cache reloaded"
            if is_fake_message:
                send_to_logger(message, "✅ Local cache reloaded automatically")
            else:
                safe_send_message_with_auto_delete(message.chat.id, final_msg, delete_after_seconds=10)
        else:
            error_msg = "❌ Error reloading local cache"
            if is_fake_message:
                send_to_logger(message, error_msg)
            else:
                safe_send_message_with_auto_delete(message.chat.id, error_msg, delete_after_seconds=10)
        return
    
    try:
        # 1) Download fresh dump via external script path
        script_path = getattr(Config, "DOWNLOAD_FIREBASE_SCRIPT_PATH", "DATABASE/download_firebase.py")
        # Ensure we have the full path to the script
        if not os.path.isabs(script_path):
            script_path = os.path.join(os.getcwd(), script_path)
        
        # Verify script exists
        if not os.path.exists(script_path):
            error_msg = safe_get_messages(message.chat.id).ADMIN_SCRIPT_NOT_FOUND_MSG.format(script_path=script_path)
            safe_send_message_with_auto_delete(message.chat.id, error_msg, delete_after_seconds=60)
            send_to_logger(message, safe_get_messages(message.chat.id).ADMIN_SCRIPT_NOT_FOUND_LOG_MSG.format(script_path=script_path))
            return
            
        # Send initial status message (skip for fake_message)
        status_msg = None
        if not is_fake_message:
            status_msg = safe_send_message(message.chat.id, safe_get_messages(message.chat.id).ADMIN_DOWNLOADING_MSG.format(script_path=script_path))
            if not status_msg:
                send_to_logger(message, safe_get_messages(message.chat.id).ADMIN_FAILED_SEND_STATUS_LOG_MSG)
                return
        
        result = subprocess.run([sys.executable, script_path], capture_output=True, text=True, encoding='utf-8', errors='replace', cwd=os.path.dirname(os.path.dirname(script_path)))
        if result.returncode != 0:
            error_msg = safe_get_messages(message.chat.id).ADMIN_ERROR_SCRIPT_MSG.format(script_path=script_path, stdout=result.stdout, stderr=result.stderr)
            if is_fake_message:
                # Do not send anything to chat on fake_message
                from HELPERS.logger import log_error_to_channel
                log_error_to_channel(message, error_msg)
                send_to_logger(message, safe_get_messages(message.chat.id).ADMIN_ERROR_RUNNING_SCRIPT_LOG_MSG.format(script_path=script_path, stdout=result.stdout, stderr=result.stderr))
            else:
                safe_edit_message_text(message.chat.id, status_msg.id, error_msg)
                # Schedule deletion after 60 seconds for real messages
                def delete_msg():
                    messages = safe_get_messages(message.chat.id)
                    time.sleep(60)
                    safe_delete_messages(message.chat.id, [status_msg.id])
                threading.Thread(target=delete_msg, daemon=True).start()
                from HELPERS.logger import log_error_to_channel
                log_error_to_channel(message, error_msg)
                send_to_logger(message, safe_get_messages(message.chat.id).ADMIN_ERROR_RUNNING_SCRIPT_LOG_MSG.format(script_path=script_path, stdout=result.stdout, stderr=result.stderr))
            return
        
        # Update status to reloading
        if is_fake_message:
            # Do not send anything to chat on fake_message
            pass
        else:
            safe_edit_message_text(message.chat.id, status_msg.id, safe_get_messages(message.chat.id).ADMIN_RELOADING_CACHE_MSG)
        
        # 2) Reload local cache into memory
        from DATABASE.cache_db import reload_firebase_cache as _reload_local
        success = _reload_local()
        if success:
            final_msg = safe_get_messages(message.chat.id).ADMIN_CACHE_RELOADED_MSG
            if is_fake_message:
                # Only log to channel/logger
                send_to_logger(message, safe_get_messages(message.chat.id).ADMIN_CACHE_RELOADED_AUTO_LOG_MSG)
            else:
                safe_edit_message_text(message.chat.id, status_msg.id, final_msg)
                # Schedule deletion after 60 seconds for real messages
                def delete_msg():
                    time.sleep(60)
                    safe_delete_messages(message.chat.id, [status_msg.id])
                threading.Thread(target=delete_msg, daemon=True).start()
                send_to_logger(message, safe_get_messages(message.chat.id).ADMIN_CACHE_RELOADED_ADMIN_LOG_MSG)
        else:
            cache_file = getattr(Config, 'FIREBASE_CACHE_FILE', 'firebase_cache.json')
            final_msg = safe_get_messages(message.chat.id).ADMIN_CACHE_FAILED_MSG.format(cache_file=cache_file)
            if is_fake_message:
                # Only log to channel/logger
                from HELPERS.logger import log_error_to_channel
                log_error_to_channel(message, final_msg)
                send_to_logger(message, final_msg)
            else:
                safe_edit_message_text(message.chat.id, status_msg.id, final_msg)
                # Schedule deletion after 60 seconds for real messages
                def delete_msg():
                    time.sleep(60)
                    safe_delete_messages(message.chat.id, [status_msg.id])
                threading.Thread(target=delete_msg, daemon=True).start()
                from HELPERS.logger import log_error_to_channel
                log_error_to_channel(message, final_msg)
    except Exception as e:
        # GLOBAL SAFETY: ensure messages is initialized
        if 'messages' not in locals() or messages is None:
            try:
                messages = safe_get_messages(message.chat.id)
            except Exception:
                # If everything fails, create a minimal fallback
                class EmergencyMessages:
                    def __getattr__(self, name):
                        return f"[{name}]"
                messages = EmergencyMessages()
        error_msg = safe_get_messages(message.chat.id).ADMIN_ERROR_RELOADING_MSG.format(error=str(e))
        # Try to update the status message if it exists, otherwise send new message
        if 'status_msg' in locals() and status_msg and not is_fake_message:
            safe_edit_message_text(message.chat.id, status_msg.id, error_msg)
            # Schedule deletion after 60 seconds
            def delete_msg():
                time.sleep(60)
                safe_delete_messages(message.chat.id, [status_msg.id])
            threading.Thread(target=delete_msg, daemon=True).start()
        else:
            # For fake messages, do not send to chat; only log
            if not is_fake_message:
                safe_send_message_with_auto_delete(message.chat.id, error_msg, delete_after_seconds=60)
        from HELPERS.logger import log_error_to_channel
        log_error_to_channel(message, error_msg)
        send_to_logger(message, safe_get_messages(message.chat.id).ADMIN_ERROR_RELOADING_CACHE_LOG_MSG.format(error=str(e)))

# SEND BRODCAST Message to All Users



# Getting the User Logs





# Get All Kinds of Users (Users/ Blocked/ Unblocked)


# Block User

def block_user(app, message):
    messages = safe_get_messages(message.chat.id)
    guard = get_channel_guard()
    if int(message.chat.id) in Config.ADMIN:
        dt = math.floor(time.time())
        parts = (message.text or "").strip().split(maxsplit=1)
        if len(parts) < 2:
            send_to_user(message, safe_get_messages(message.chat.id).ADMIN_BLOCK_USER_USAGE_MSG)
            return
        argument = parts[1].strip()
        argument_lower = argument.lower()

        # Channel guard helpers
        if guard:
            if argument_lower == "show":
                # Use 48 hours (2 days) instead of 3 days
                hours_span = 48
                # Check access to admin logs
                if not guard.can_read_admin_log():
                    safe_send_message(
                        message.chat.id,
                        messages.CHANNEL_GUARD_NO_ACCESS_MSG,
                        message=message,
                    )
                    return
                activity_entries = guard.export_recent_activity(hours=hours_span)
                if activity_entries:
                    join_total = sum(1 for item in activity_entries if item.get("type") == "join")
                    leave_total = sum(1 for item in activity_entries if item.get("type") == "leave")
                    lines = []
                    for entry in activity_entries:
                        emoji = "🟢" if entry.get("type") == "join" else "🔴"
                        dt_local = datetime.fromtimestamp(entry.get("timestamp", 0))
                        time_str = dt_local.strftime("%Y-%m-%d %H:%M:%S")
                        user_id = entry.get("user_id")
                        username = entry.get("username")
                        username_part = f"@{username}" if username else ""
                        name_part = entry.get("name") or ""
                        display = (name_part + f" {username_part}").strip()
                        if not display:
                            display = f"ID {user_id}"
                        description = entry.get("description") or ""
                        # Include Telegram ID in the line
                        lines.append(f"{emoji} {time_str} — {display} (ID: {user_id}) {description}".strip())
                    lines.append("")
                    lines.append(
                        messages.CHANNEL_GUARD_ACTIVITY_TOTALS_LINE_MSG.format(
                            joined=join_total,
                            left=leave_total,
                        )
                    )
                    user_dir = os.path.join("users", str(message.chat.id))
                    os.makedirs(user_dir, exist_ok=True)
                    file_path = os.path.join(user_dir, f"channel_activity_{int(time.time())}.txt")
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write("\n".join(lines))
                    try:
                        app.send_document(
                            message.chat.id,
                            file_path,
                            caption=messages.CHANNEL_GUARD_ACTIVITY_FILE_CAPTION_MSG.format(hours=hours_span),
                            reply_to_message_id=message.id,
                        )
                    except Exception as e:
                        logger.error(f"[block_user_show] Failed to send activity file: {e}")
                    safe_send_message(
                        message.chat.id,
                        messages.CHANNEL_GUARD_ACTIVITY_SUMMARY_MSG.format(hours=hours_span, joined=join_total, left=leave_total),
                        message=message,
                    )
                else:
                    safe_send_message(
                        message.chat.id,
                        messages.CHANNEL_GUARD_ACTIVITY_EMPTY_MSG.format(hours=hours_span),
                        message=message,
                    )

                # Show the pending queue only if it's not empty
                pending = guard.get_pending_leavers()
                if not pending:
                    # Do not show an "empty queue" message if activity was already displayed
                    return
                rows = [messages.CHANNEL_GUARD_PENDING_HEADER_MSG.format(total=len(pending))]
                for entry in pending[:50]:
                    left_ts = int(entry.get("last_left_ts", entry.get("first_left_ts", 0)))
                    try:
                        left_dt = datetime.fromtimestamp(left_ts)
                        left_text = left_dt.strftime("%Y-%m-%d %H:%M:%S")
                    except Exception:
                        left_text = str(left_ts)
                    rows.append(
                        messages.CHANNEL_GUARD_PENDING_ROW_MSG.format(
                            user_id=entry.get("ID"),
                            name=entry.get("name") or entry.get("username") or "-",
                            username=entry.get("username") or "-",
                            last_left=left_text,
                        )
                    )
                if len(pending) > 50:
                    rows.append(messages.CHANNEL_GUARD_PENDING_MORE_MSG.format(extra=len(pending) - 50))
                rows.append(messages.CHANNEL_GUARD_PENDING_FOOTER_MSG)
                safe_send_message(message.chat.id, "\n".join(rows), message=message)
                return
            if argument_lower == "all":
                # Get all users who left the channel in the last 48 hours
                hours_span = 48
                if not guard.can_read_admin_log():
                    safe_send_message(
                        message.chat.id,
                        messages.CHANNEL_GUARD_NO_ACCESS_MSG,
                        message=message,
                    )
                    return
                
                activity_entries = guard.export_recent_activity(hours=hours_span)
                # Collect IDs of all users who left the channel (type "leave")
                leave_user_ids = set()
                for entry in activity_entries:
                    if entry.get("type") == "leave":
                        user_id = entry.get("user_id")
                        if user_id:
                            leave_user_ids.add(int(user_id))
                
                # Also include pending leavers (in case they haven't appeared in activity yet)
                pending_ids = guard.get_pending_ids()
                for uid in pending_ids:
                    leave_user_ids.add(uid)
                
                if not leave_user_ids:
                    safe_send_message(message.chat.id, messages.CHANNEL_GUARD_PENDING_EMPTY_MSG, message=message)
                    return
                
                # Check who is already blocked
                snapshot = db.child(f"{Config.BOT_DB_PATH}/blocked_users").get()
                all_blocked_users = snapshot.each() if snapshot else []
                blocked_ids = {int(str(b_user.key())) for b_user in (all_blocked_users or []) if b_user is not None}
                
                # Keep only users who are not blocked yet
                to_block_ids = [uid for uid in leave_user_ids if uid not in blocked_ids]
                
                if not to_block_ids:
                    safe_send_message(
                        message.chat.id,
                        messages.CHANNEL_GUARD_BLOCKED_ALL_MSG.format(count=0),
                        message=message,
                    )
                    return
                
                total_processed = 0
                for uid in to_block_ids:
                    try:
                        fake_msg = fake_message(
                            f"{Config.BLOCK_USER_COMMAND} {uid}",
                            message.chat.id,
                            original_chat_id=message.chat.id,
                            message_thread_id=getattr(message, "message_thread_id", None),
                            original_message=message,
                        )
                        block_user(app, fake_msg)
                        total_processed += 1
                    except Exception as e:
                        logger.error(f"[block_user_all] Failed to block user {uid}: {e}")
                
                safe_send_message(
                    message.chat.id,
                    messages.CHANNEL_GUARD_BLOCKED_ALL_MSG.format(count=total_processed),
                    message=message,
                )
                return
            if argument_lower == "auto":
                enabled = guard.toggle_auto_mode()
                status_msg = (
                    messages.CHANNEL_GUARD_AUTO_ENABLED_MSG
                    if enabled
                    else messages.CHANNEL_GUARD_AUTO_DISABLED_MSG
                )
                safe_send_message(message.chat.id, status_msg, message=message)
                return
            if argument_lower == "auto off":
                guard.set_auto_mode(False)
                safe_send_message(message.chat.id, messages.CHANNEL_GUARD_AUTO_DISABLED_MSG, message=message)
                return
            try:
                seconds = parse_period_to_seconds(argument)
                guard.set_auto_interval(seconds)
                safe_send_message(
                    message.chat.id,
                    messages.CHANNEL_GUARD_AUTO_INTERVAL_SET_MSG.format(interval=format_seconds_human(seconds)),
                    message=message,
                )
                return
            except ValueError:
                pass

        b_user_id = argument
        try:
            if int(b_user_id) in Config.ADMIN:
                send_to_all(message, safe_get_messages(message.chat.id).ADMIN_CANNOT_DELETE_ADMIN_MSG)
                return
        except Exception:
            pass

        snapshot = db.child(f"{Config.BOT_DB_PATH}/blocked_users").get()
        all_blocked_users = snapshot.each() if snapshot else []
        b_users = [str(b_user.key()) for b_user in (all_blocked_users or []) if b_user is not None]

        if b_user_id not in b_users:
            data = {"ID": b_user_id, "timestamp": str(dt)}
            db.child(f"{Config.BOT_DB_PATH}/blocked_users/{b_user_id}").set(data)
            send_to_user(message, safe_get_messages(message.chat.id).ADMIN_USER_BLOCKED_MSG.format(user_id=b_user_id, date=datetime.fromtimestamp(dt)))
            if guard:
                guard.mark_user_blocked(b_user_id, reason="manual")
        else:
            send_to_user(message, safe_get_messages(message.chat.id).ADMIN_USER_ALREADY_BLOCKED_MSG.format(user_id=b_user_id))
    else:
        send_to_all(message, safe_get_messages(message.chat.id).ADMIN_NOT_ADMIN_MSG)


# Unblock User

def unblock_user(app, message):
    messages = safe_get_messages(message.chat.id)
    guard = get_channel_guard()
    if int(message.chat.id) in Config.ADMIN:
        parts = (message.text or "").strip().split(maxsplit=1)
        if len(parts) < 2:
            send_to_user(message, safe_get_messages(message.chat.id).ADMIN_UNBLOCK_USER_USAGE_MSG)
            return
        ub_user_id = parts[1].strip()

        if ub_user_id.lower() == "all":
            snapshot = db.child(f"{Config.BOT_DB_PATH}/blocked_users").get()
            all_blocked_users = snapshot.each() if snapshot else []
            user_ids = [
                str(b_user.key())
                for b_user in (all_blocked_users or [])
                if b_user is not None and str(b_user.key()) not in ("0", "")
            ]
            if not user_ids:
                send_to_user(message, messages.CHANNEL_GUARD_PENDING_EMPTY_MSG)
                return
            dt = math.floor(time.time())
            for user_id in user_ids:
                data = {"ID": user_id, "timestamp": str(dt)}
                db.child(f"{Config.BOT_DB_PATH}/unblocked_users/{user_id}").set(data)
                db.child(f"{Config.BOT_DB_PATH}/blocked_users/{user_id}").remove()
                if guard:
                    guard.record_manual_unblock(user_id)
            send_to_user(
                message,
                messages.ADMIN_UNBLOCK_ALL_DONE_MSG.format(count=len(user_ids), date=datetime.fromtimestamp(dt)),
            )
            return

        snapshot = db.child(f"{Config.BOT_DB_PATH}/blocked_users").get()
        all_blocked_users = snapshot.each() if snapshot else []
        b_users = [str(b_user.key()) for b_user in (all_blocked_users or []) if b_user is not None]

        if ub_user_id in b_users:
            dt = math.floor(time.time())

            data = {"ID": ub_user_id, "timestamp": str(dt)}
            db.child(f"{Config.BOT_DB_PATH}/unblocked_users/{ub_user_id}").set(data)
            db.child(f"{Config.BOT_DB_PATH}/blocked_users/{ub_user_id}").remove()
            send_to_user(
                message, safe_get_messages(message.chat.id).ADMIN_USER_UNBLOCKED_MSG.format(user_id=ub_user_id, date=datetime.fromtimestamp(dt)))
            if guard:
                guard.record_manual_unblock(ub_user_id)

        else:
            send_to_user(message, safe_get_messages(message.chat.id).ADMIN_USER_ALREADY_UNBLOCKED_MSG.format(user_id=ub_user_id))
    else:
        send_to_all(message, safe_get_messages(message.chat.id).ADMIN_NOT_ADMIN_MSG)




# Check Runtime



def _channel_guard_block_executor(user_id: int, reason: Optional[str] = None):
    admins = getattr(Config, "ADMIN", [])
    if not admins:
        return
    admin_id = admins[0]
    fake_msg = fake_message(f"{Config.BLOCK_USER_COMMAND} {user_id}", admin_id)
    fake_msg._auto_reason = reason
    block_user(app, fake_msg)


register_block_user_executor(_channel_guard_block_executor)



def uncache_command(app, message):
    messages = safe_get_messages(message.chat.id)
    """
    Admin command to clear cache for a specific URL
    Usage: /uncache <URL>
    """
    # Lazy imports to avoid cycles
    from DATABASE.cache_db import get_url_hash
    from DATABASE.firebase_init import db_child_by_path

    user_id = message.chat.id
    text = message.text.strip()
    if len(text.split()) < 2:
        send_to_user(message, safe_get_messages(message.chat.id).ADMIN_UNCACHE_USAGE_MSG)
        return
    url = text.split(maxsplit=1)[1].strip()
    if not url.startswith("http://") and not url.startswith("https://"):
        send_to_user(message, safe_get_messages(message.chat.id).ADMIN_UNCACHE_INVALID_URL_MSG)
        return
    removed_any = False
    try:
        # Clearing the cache by video
        normalized_url = normalize_url_for_cache(url)
        url_hash = get_url_hash(normalized_url)
        video_cache_path = f"{Config.VIDEO_CACHE_DB_PATH}/{url_hash}"
        db_child_by_path(db, video_cache_path).remove()
        removed_any = True
        # Clear cache by image posts (for /img)
        try:
            img_cache_path = f"{Config.IMAGE_CACHE_DB_PATH}/{url_hash}"
            db_child_by_path(db, img_cache_path).remove()
            removed_any = True
        except Exception:
            pass
        # Clear cache by playlist (if any)
        playlist_url = get_clean_playlist_url(url)
        if playlist_url:
            playlist_normalized = normalize_url_for_cache(playlist_url)
            playlist_hash = get_url_hash(playlist_normalized)
            playlist_cache_path = f"{Config.PLAYLIST_CACHE_DB_PATH}/{playlist_hash}"
            db_child_by_path(db, playlist_cache_path).remove()
            removed_any = True
            # If there is a range (eg *1*5), clear the cache for each index
            m = re.search(r"\*(\d+)\*(\d+)", url)
            if m:
                start, end = int(m.group(1)), int(m.group(2))
                for idx in range(start, end + 1):
                    idx_path = f"{Config.PLAYLIST_CACHE_DB_PATH}/{playlist_hash}/{idx}"
                    db_child_by_path(db, idx_path).remove()
        # Clear cache for short/long YouTube links
        if is_youtube_url(url):
            short_url = youtube_to_short_url(url)
            long_url = youtube_to_long_url(url)
            for variant in [short_url, long_url]:
                norm = normalize_url_for_cache(variant)
                h = get_url_hash(norm)
                db_child_by_path(db, f"{Config.VIDEO_CACHE_DB_PATH}/{h}").remove()
                db_child_by_path(db, f"{Config.PLAYLIST_CACHE_DB_PATH}/{h}").remove()
        # Update local cache after deletion in local mode
        use_firebase = getattr(Config, 'USE_FIREBASE', True)
        if not use_firebase and removed_any:
            from DATABASE.cache_db import reload_firebase_cache
            reload_firebase_cache()
        
        if removed_any:
            send_to_user(message, safe_get_messages(message.chat.id).ADMIN_CACHE_CLEARED_MSG.format(url=url))
            send_to_logger(message, safe_get_messages(message.chat.id).ADMIN_CACHE_CLEARED_LOG_MSG.format(user_id=user_id, url=url))
        else:
            send_to_user(message, safe_get_messages(message.chat.id).ADMIN_NO_CACHE_FOUND_MSG)
    except Exception as e:
        send_to_all(message, safe_get_messages(message.chat.id).ADMIN_ERROR_CLEARING_CACHE_MSG.format(error=e))






