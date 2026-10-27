"""❌ Cancel button on the downloading / uploading card.

One flag per user: the button sets it, download and upload progress checks it.
Started downloads clear it first, so an old tap never stops the next download.
"""
from __future__ import annotations

import threading

_flags = set()
_lock = threading.Lock()


class Cancelled(RuntimeError):
    """The user tapped Cancel."""


def request(user_id) -> None:
    with _lock:
        _flags.add(int(user_id))


def clear(user_id) -> None:
    with _lock:
        _flags.discard(int(user_id))


def is_set(user_id) -> bool:
    with _lock:
        return int(user_id) in _flags


def check(user_id) -> None:
    if is_set(user_id):
        raise Cancelled("Download cancelled by the user")


def stop_upload(user_id) -> None:
    """Inside a Pyrogram upload progress callback: abort the upload when cancelled."""
    if is_set(user_id):
        from HELPERS.app_instance import get_app
        get_app().stop_transmission()


def markup(user_id):
    from pyrogram.enums import ButtonStyle
    from pyrogram.types import InlineKeyboardMarkup
    from COMMANDS.ui_kit import btn
    return InlineKeyboardMarkup([[btn("ban", "Cancel", cb=f"cx|{int(user_id)}", style=ButtonStyle.DANGER)]])
