import asyncio
from pyrogram import enums
from pyrogram.parser import Parser
from HELPERS import progress_ui as ui


def test_bar_bounds():
    assert ui.bar(0) == "▭" * 12 and ui.bar(100) == "▬" * 12 and ui.bar(250) == "▬" * 12 and ui.bar(-5) == "▭" * 12
    assert ui.bar_blocks(3) == "▬▬▬" + "▭" * 7


def test_card_plain_by_default(monkeypatch):
    monkeypatch.delenv("PREMIUM_EMOJI_ENABLED", raising=False)
    t = ui.progress_card("search", 0)
    assert "<tg-emoji" not in t and "<blockquote>" in t and "Searching" in t and "0%" in t


def test_card_premium_and_escape(monkeypatch):
    monkeypatch.setenv("PREMIUM_EMOJI_ENABLED", "true")
    monkeypatch.setenv("PREMIUM_EMOJI_DOWNLOAD", "5368324170671202286")
    t = ui.progress_card("download", 50, "<b>1/3</b>", "5 MB/s")
    assert 'emoji-id="5368324170671202286"' in t and "&lt;b&gt;1/3" in t
    assert "<tg-emoji" not in ui.strip_premium(t)
    ents = asyncio.run(Parser(None).parse(t, enums.ParseMode.HTML))["entities"]
    names = {type(e).__name__ for e in ents}
    assert {"MessageEntityCustomEmoji", "MessageEntityBlockquote"} <= names


def test_bad_premium_id_falls_back(monkeypatch):
    monkeypatch.setenv("PREMIUM_EMOJI_ENABLED", "true")
    monkeypatch.setenv("PREMIUM_EMOJI_UPLOAD", "not-a-number")
    assert "<tg-emoji" not in ui.progress_card("upload", 10)


def test_edit_card_retries_without_premium(monkeypatch):
    monkeypatch.setenv("PREMIUM_EMOJI_ENABLED", "true")
    monkeypatch.setenv("PREMIUM_EMOJI_DOWNLOAD", "123")
    calls = []
    def fake(chat, mid, text, **kw):
        calls.append(text)
        return None if "<tg-emoji" in text else object()
    assert ui.edit_card(fake, 1, 2, ui.progress_card("download", 5)) is not None
    assert len(calls) == 2 and "<tg-emoji" not in calls[1]
