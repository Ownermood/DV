import time
from types import SimpleNamespace as NS

import pytest
from HELPERS import app_instance


class _FakeApp:
    def __getattr__(self, name):
        return lambda *a, **k: (lambda f: f)


app_instance.set_app(_FakeApp())
from COMMANDS import admin_panel as ap  # noqa: E402


class _Client:
    def __init__(self, fail=()):
        self.n, self.copies, self.fail = 100, [], set(fail)

    def _m(self, chat):
        self.n += 1
        return NS(id=self.n, chat=NS(id=chat), photo=None)

    def send_message(self, chat, text, **k):
        return self._m(chat)

    def edit_message_text(self, chat, mid, text, **k):
        return self._m(chat)

    def get_messages(self, chat, mid):
        return NS(reply_markup=None)

    def copy_message(self, uid, chat, mid, **k):
        if uid in self.fail:
            raise RuntimeError("USER_IS_BLOCKED")
        self.copies.append(uid)
        return self._m(uid)


@pytest.fixture
def env(monkeypatch):
    state = {}
    monkeypatch.setattr(ap, "state_get", lambda k, d=None: state.get(k, d))
    monkeypatch.setattr(ap, "state_set", lambda k, v: state.__setitem__(k, v))
    monkeypatch.setattr(ap, "all_users", lambda: [1, 2, 3, 4])
    monkeypatch.setattr(ap, "blocked_users", lambda: {4})
    monkeypatch.setattr(ap, "_db_users", lambda: {})
    from COMMANDS import groups_store
    monkeypatch.setattr(groups_store, "all_groups", lambda: [])
    monkeypatch.setattr(ap, "_is_admin", lambda uid: uid == 1)
    ap._bc_job.update(on=False, stop=False)
    ap._drafts.clear()
    return state


def _wait():
    for _ in range(50):
        if not ap._bc_job["on"]:
            return
        time.sleep(0.05)


def test_broadcast_reaches_every_unblocked_user(env):
    c = _Client(fail={3})
    ap._input[1] = "bc"
    assert ap.handle_admin_input(c, NS(id=50, chat=NS(id=1), text="hello", media_group_id=None))
    assert ap._start_broadcast(None, c, 1, False) == ""
    _wait()
    assert c.copies == [1, 2] and not ap._bc_job["on"]       # 3 blocked the bot, 4 is blocked
    rec = env["broadcasts"][-1]
    assert rec["sent"] == 2 and rec["status"] == "done"


def test_draft_survives_a_restart(env):
    ap._put_draft(1, {"chat": 1, "id": 9, "album": False})
    ap._drafts.clear()                                         # process restarted
    c = _Client()
    assert ap._start_broadcast(None, c, 1, False) == ""
    _wait()
    assert c.copies == [1, 2, 3]
    assert ap._start_broadcast(None, c, 1, False) == "Draft expired. Start again."   # one use


def test_error_never_leaves_broadcast_stuck(env, monkeypatch):
    monkeypatch.setattr(ap, "all_users", lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    ap._put_draft(1, {"chat": 1, "id": 9, "album": False})
    assert ap._start_broadcast(None, _Client(), 1, False) == ""
    _wait()
    assert ap._bc_job["on"] is False      # the next broadcast can start


def test_broadcast_goes_to_groups_too(env, monkeypatch):
    from COMMANDS import groups_store
    monkeypatch.setattr(groups_store, "all_groups", lambda: [-100111, -100222])
    monkeypatch.setattr(ap, "_db_users", lambda: {2: 0, -100333: 0})     # old versions saved a group as a user
    forgotten = []
    monkeypatch.setattr(groups_store, "forget", forgotten.append)
    c = _Client(fail={-100222})
    c.copy_message_orig = c.copy_message

    def copy(uid, chat, mid, **k):
        if uid == -100222:
            raise RuntimeError("CHAT_WRITE_FORBIDDEN")
        return c.copy_message_orig(uid, chat, mid, **k)
    c.copy_message = copy
    ap._put_draft(1, {"chat": 1, "id": 9, "album": False})
    assert ap._start_broadcast(None, c, 1, False) == ""
    _wait()
    assert c.copies == [1, 2, 3, -100333, -100111]          # users, then groups
    assert forgotten == [-100222]                             # removed group is dropped from the list


def test_groups_store_remember_and_forget(monkeypatch):
    from COMMANDS import groups_store as gs
    state = {}
    monkeypatch.setattr(gs, "state_get", lambda k, d=None: state.get(k, d))
    monkeypatch.setattr(gs, "state_set", lambda k, v: state.__setitem__(k, v))
    gs._known.clear()
    gs.remember(NS(id=-1001, title="Fans"))
    gs.remember(NS(id=55, title="private"))                  # not a group
    assert gs.all_groups() == [-1001]
    gs.forget(-1001)
    assert gs.all_groups() == []
