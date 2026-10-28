import time
from types import SimpleNamespace as NS

from HELPERS import group_mirror as gm


class _App:
    def __init__(self):
        self.copies, self.deleted, self.sent = [], [], []

    def send_video(self, chat_id, video, **kw):
        self.sent.append(chat_id)
        return NS(id=50)

    def copy_message(self, chat, from_chat, mid, **kw):
        self.copies.append((chat, from_chat, mid, kw))
        return NS(id=600)

    def delete_messages(self, chat, ids):
        self.deleted.append((chat, ids))


def test_dm_file_is_copied_to_group_and_removed(monkeypatch):
    monkeypatch.setattr(gm, "GROUP_COPY_TTL", 0.05)
    app = _App()
    gm.install(app)
    gm.install(app)   # twice is harmless
    group_msg = NS(id=7, chat=NS(id=-100), message_thread_id=33, is_topic_message=True)
    app.send_video(42, "a.mp4")                       # no group job: nothing copied
    gm.start(42, group_msg)
    app.send_video(chat_id=42, video="v.mp4")
    time.sleep(0.2)
    assert app.copies == [(-100, 42, 50, {"message_thread_id": 33})]   # same topic
    assert (-100, [600]) in app.deleted                                 # group copy gone after the TTL
    gm.stop(app, 42)
    time.sleep(0.2)
    assert (-100, [7]) in app.deleted and gm.active(42) is None          # the /dl message goes too
