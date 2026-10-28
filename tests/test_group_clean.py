import time
from types import SimpleNamespace as NS

from pyrogram.types import ReplyParameters

from HELPERS import group_clean as gc


class _App:
    def __init__(self):
        self.sent, self.deleted = [], []

    def send_video(self, chat_id, video, **kw):
        self.sent.append((chat_id, kw))
        return NS(id=99)

    def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, kw))
        return NS(id=98)

    def delete_messages(self, chat, ids):
        self.deleted.append((chat, ids))


def _msg(chat, mid, user):
    return NS(chat=NS(id=chat), id=mid, from_user=NS(id=user))


def test_group_file_drops_reply_mentions_and_deletes_request():
    app = _App()
    gc.install(app)
    gc.install(app)  # twice is harmless
    gc.track(_msg(-100, 7, 555))
    app.send_video(chat_id=-100, video="v.mp4", caption="<blockquote expandable>Title</blockquote>",
                   reply_parameters=ReplyParameters(message_id=7))
    chat, kw = app.sent[-1]
    assert "reply_parameters" not in kw
    assert kw["caption"].startswith('<blockquote expandable><a href="tg://user?id=555">​</a>Title')
    time.sleep(0.2)
    assert app.deleted == [(-100, 7)]
    app.send_video(-100, "part2.mp4", caption="x", reply_to_message_id=7)   # next part: deleted once only
    time.sleep(0.2)
    assert app.deleted == [(-100, 7)] and "reply_to_message_id" not in app.sent[-1][1]


def test_private_and_untracked_untouched():
    app = _App()
    gc.install(app)
    gc.track(_msg(55, 1, 55))  # private chat: never tracked
    app.send_video(55, "v.mp4", caption="c", reply_to_message_id=1)
    app.send_video(-100, "v.mp4", caption="c", reply_to_message_id=12345)
    assert all(kw["caption"] == "c" and kw["reply_to_message_id"] for _, kw in app.sent)
    app.send_message(-100, "progress", reply_to_message_id=7)   # text messages are never changed
    time.sleep(0.1)
    assert app.deleted == []


def test_hidden_mention_skips_full_caption_and_plain_mode():
    from pyrogram import enums
    assert gc.hidden_mention("a" * 1010, 1) == "a" * 1010
    assert gc.hidden_mention("x", 1, enums.ParseMode.DISABLED) == "x"
    assert gc.hidden_mention(None, 1) == '<a href="tg://user?id=1">​</a>'
