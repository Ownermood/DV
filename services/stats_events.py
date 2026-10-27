"""No-op stats hooks.

The web dashboard that consumed these events was removed; the hooks stay so
callers keep working without collecting anything."""
from typing import Any




def emit_download_event(*args, **kwargs) -> None:
    return None


def capture_message_context(message) -> None:
    return None


def update_download_progress(*args, **kwargs) -> None:
    return None


def wrap_db_adapter(db_adapter: Any) -> Any:
    return db_adapter
