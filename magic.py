"""Downloder bot entry point.

Handlers register themselves when their module is imported:
  COMMANDS/simple_ui    front door (group -1): /start, /help, links, preview cards, buttons
  COMMANDS/admin_panel  admin panel (group -1)
  URL_PARSERS/url_extractor  the download pipeline for links that pass the front door
"""
# Guard against "name 'messages' is not defined" in the old modules
try:
    from PATCH.GLOBAL_MESSAGES_PATCH import apply_global_messages_patch
    apply_global_messages_patch()
except Exception as e:
    print(f"⚠️  Global messages patch failed: {e}")

import atexit
import signal
import sys

from pyrogram import Client, idle

from CONFIG.config import Config
from CONFIG.messages import safe_get_messages
from HELPERS.app_instance import set_app
from HELPERS.channel_guard import start_channel_guard, stop_channel_guard

app = Client(
    "magic",
    api_id=Config.API_ID,
    api_hash=Config.API_HASH,
    bot_token=Config.BOT_TOKEN,
    workers=32,                       # handlers for many users at once
    max_concurrent_transmissions=8,   # default 1 = every user's upload waits for the previous one
)
set_app(app)  # before any module with handlers is imported
from HELPERS.group_mirror import install as _group_mirror  # noqa: E402
_group_mirror(app)  # group requests: files sent to the DM are copied to the group (1 min)

from DATABASE.cache_db import start_auto_cache_reloader  # noqa: E402
import DATABASE.firebase_init  # noqa: E402,F401  (database: MongoDB / local JSON)
import URL_PARSERS.url_extractor  # noqa: E402,F401  (download pipeline)
from COMMANDS import simple_ui  # noqa: E402,F401  (front door)
from COMMANDS import admin_panel  # noqa: E402,F401  (admin panel)
from COMMANDS import user_prefs  # noqa: E402,F401  (user settings: thumbnail, default quality)

print(safe_get_messages(None).MAGIC_ALL_MODULES_LOADED_MSG)

# Run the automatic loading of the Firebase cache (only if Firebase is enabled)
use_firebase = getattr(Config, 'USE_FIREBASE', True)
if use_firebase:
    start_auto_cache_reloader()
else:
    print("ℹ️ Automatic cache reload is disabled (local mode)")

def cleanup_on_exit():
    messages = safe_get_messages(None)
    """Cleanup function to close Firebase connections, HTTP sessions and logger on exit"""
    try:
        # Close all HTTP sessions first
        try:
            from HELPERS.http_manager import close_all_sessions
            close_all_sessions()
            print("✅ HTTP sessions closed")
        except Exception as e:
            print(f"⚠️ Error closing HTTP sessions: {e}")
        
        # Close Firebase connections
        from DATABASE.cache_db import close_all_firebase_connections
        close_all_firebase_connections()
        
        # Close logger handlers
        try:
            from HELPERS.logger import close_logger
            close_logger()
        except Exception as e:
            print(messages.MAGIC_ERROR_CLOSING_LOGGER_MSG.format(error=e))
        
        print(messages.MAGIC_CLEANUP_COMPLETED_MSG)
    except Exception as e:
        print(messages.MAGIC_ERROR_DURING_CLEANUP_MSG.format(error=e))

# Register cleanup function
atexit.register(cleanup_on_exit)

# Register signal handlers for graceful shutdown
def signal_handler(sig, frame):
    messages = safe_get_messages(None)
    """Handle shutdown signals gracefully"""
    print(messages.MAGIC_SIGNAL_RECEIVED_MSG.format(signal=sig))
    cleanup_on_exit()
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

if __name__ == "__main__":
    app.start()
    try:  # public menu = start/help only; admin menu only in admins' chats
        from COMMANDS.simple_ui import _ensure_menu
        _ensure_menu(app)
        from COMMANDS.ui_kit import verify_premium
        from COMMANDS.admin_panel import OWNERS
        verify_premium(app, sorted(OWNERS))
    except Exception as e:
        print(f"menu setup failed: {e}")
    start_channel_guard(app)
    from HELPERS.alerts import start_health_watch
    start_health_watch()  # DMs the owners when the token provider / MongoDB / disk breaks or recovers
    idle()
    try:
        app.loop.run_until_complete(stop_channel_guard())
    except Exception:
        pass
    app.stop()
