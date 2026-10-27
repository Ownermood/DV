import asyncio
from pyrogram import enums
from pyrogram.parser import Parser
from HELPERS import progress_ui as ui
from COMMANDS import ui_kit


def _premium(monkeypatch, on):
    monkeypatch.setattr(ui_kit, "premium_on", lambda: on)
    monkeypatch.setattr(ui_kit, "state_get", lambda k, d=None: on if k == "premium_ok" else d)


def test_bar_five_blocks_twenty_percent_steps():
    assert ui.bar(0) == "▱" * 5 and ui.bar(100) == "▰" * 5 and ui.bar(250) == "▰" * 5 and ui.bar(-5) == "▱" * 5
    assert ui.bar(47) == "▰▰▱▱▱" and ui.step(47) == 40 and ui.step(99.9) == 80


def test_card_plain_when_premium_not_verified(monkeypatch):
    _premium(monkeypatch, False)
    t = ui.progress_card("search", 0)
    assert "<emoji" not in t and "<blockquote>" in t and "Searching" in t and "0%" in t


def test_card_premium_ignores_old_process_text(monkeypatch):
    _premium(monkeypatch, True)
    t = ui.progress_card("download", 57, "<b>📶 Total Progress</b>", "5 MB/s")
    assert '<emoji id="' in t and "Total Progress" not in t and "40%" in t and "5 MB/s" in t
    ents = asyncio.run(Parser(None).parse(t, enums.ParseMode.HTML))["entities"]
    names = {type(e).__name__ for e in ents}
    assert {"MessageEntityCustomEmoji", "MessageEntityBlockquote"} <= names


def test_normalize_old_statuses(monkeypatch):
    _premium(monkeypatch, False)
    assert "Downloading" in ui.normalize_status("<b>📶 Total Progress</b>\n<blockquote>Video: 1 / 1</blockquote>\n> <i>📥 Downloading using format: bv*</i>")
    assert "Processing" in ui.normalize_status("<b>📶 Total Progress</b>\n▬▬▬▬▬▬▬▬▬▬   100.0%")
    assert "Uploading" in ui.normalize_status("x\n📤 Uploading audio file...\n▬▬▬   100.0%")
    assert "Searching" in ui.normalize_status("⌛ Please wait...")
    assert ui.normalize_status("✅ Cookies saved") is None


def test_edit_card_retries_without_premium(monkeypatch):
    _premium(monkeypatch, True)
    calls = []

    def fake(chat, mid, text, **kw):
        calls.append(text)
        return None if "<emoji" in text else object()
    assert ui.edit_card(fake, 1, 2, ui.progress_card("download", 5)) is not None
    assert len(calls) == 2 and "<emoji" not in calls[1]
