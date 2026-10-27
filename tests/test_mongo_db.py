import json

import mongomock
import pytest

from DATABASE import mongo_db as m


@pytest.fixture
def mdb(monkeypatch):
    client = mongomock.MongoClient()
    db = client["t"]
    monkeypatch.setattr(m, "_db", db)
    monkeypatch.setenv("MONGO_URI", "mongodb://fake")
    return db


def test_tree_users_like_firebase(mdb):
    root = m.MongoTreeAdapter("/")
    users = root.child("bot").child("b").child("users")
    users.child("1").set({"ID": 1, "timestamp": 10})
    users.child("2").set({"ID": 2, "timestamp": 20})
    each = {c.key(): c.val() for c in users.get().each()}
    assert each == {"1": {"ID": 1, "timestamp": 10}, "2": {"ID": 2, "timestamp": 20}}
    assert mdb["tree"].count_documents({}) == 2                       # one document per user
    users.child("2").remove()
    assert list(users.get().val()) == ["1"]
    users.child("1").update({"timestamp": 11})
    assert users.child("1").get().val() == {"ID": 1, "timestamp": 11}
    assert root.child("bot", "b", "users", "1", "ID").get().val() == 1   # read inside a document
    assert m.read_path(["bot", "b", "users", "1"]) == {"ID": 1, "timestamp": 11}
    assert root.child("nothing").get().val() is None


def test_set_replaces_subtree_and_push(mdb):
    root = m.MongoTreeAdapter("/")
    root.child("bot", "video_cache", "h1").set({"720p": "5"})
    root.child("bot", "video_cache", "h1", "1080p").set("9")             # inside an existing document
    assert root.child("bot", "video_cache", "h1").get().val() == {"720p": "5", "1080p": "9"}
    root.child("bot", "video_cache").set({"h2": {"best": "1"}})          # replaces the whole branch
    assert root.child("bot", "video_cache").get().val() == {"h2": {"best": "1"}}
    key = root.child("logs").push({"a": 1})
    assert root.child("logs", key).get().val() == {"a": 1}


def test_kv_and_broadcast_records(mdb):
    m.kv_set("premium", True)
    assert m.kv_get("premium") is True and m.kv_get("missing", 5) == 5
    m.kv_set("broadcasts", [{"id": "a", "ts": 1, "items": [[1, [2]]]}, {"id": "b", "ts": 2, "items": []}])
    m.kv_set("broadcasts", [{"id": "b", "ts": 2, "items": [], "deleted": True}])
    recs = m.kv_get("broadcasts")
    assert [r["id"] for r in recs] == ["b"] and recs[0]["deleted"] is True


def test_import_old_files(mdb, tmp_path):
    dump = tmp_path / "dump.json"
    dump.write_text(json.dumps({"bot": {"b": {"users": {"7": {"ID": 7}, "8": {"ID": 8}}, "blocked_users": {"8": {}}},
                                        "video_cache": {"h": {"720p": "1"}}}}))
    state = tmp_path / "s.json"
    state.write_text(json.dumps({"admins": {"5": {"name": "x"}}}))
    m.import_old_files(str(dump), str(state))
    root = m.MongoTreeAdapter("/")
    assert sorted(c.key() for c in root.child("bot", "b", "users").get().each()) == ["7", "8"]
    assert root.child("bot", "video_cache", "h", "720p").get().val() == "1"
    assert m.kv_get("admins") == {"5": {"name": "x"}}
    m.import_old_files(str(dump), str(state))                           # only once
    assert mdb["tree"].count_documents({"_id": {"$regex": "^bot/b/users/"}}) == 2
