"""MongoDB backend (used when MONGO_URI is set).

1. MongoTreeAdapter: the same chained API as the Firebase / local JSON adapters
   (db.child("bot").child(name).child("users").get().each() ...). The tree is stored as
   path documents in the "tree" collection: {_id: "bot/x/users/123", v: {...}}. A value is
   stored at the path it was written to; a document never sits under another document,
   so "all users" is one indexed prefix query instead of loading one big JSON file.
2. kv_get / kv_set: small settings (admins, cookie manager, premium flags) in "ui_state";
   broadcast records get their own collection.
3. On first start an existing dump.json and CONFIG/.ui_state.json are imported.
"""
from __future__ import annotations

import json
import os
import re
import threading
from typing import Any

from HELPERS.logger import logger

_client = None
_db = None
_lock = threading.RLock()


def enabled() -> bool:
    return bool(os.getenv("MONGO_URI", "").strip())


def database():
    """The pymongo database (connects once)."""
    global _client, _db
    if _db is None:
        with _lock:
            if _db is None:
                from pymongo import MongoClient
                uri = os.getenv("MONGO_URI", "").strip()
                name = os.getenv("MONGO_DB", "").strip() or os.getenv("BOT_NAME", "downloader").strip() or "downloader"
                _client = MongoClient(uri, serverSelectionTimeoutMS=15000, tz_aware=False)
                _client.admin.command("ping")
                _db = _client[re.sub(r"[^\w-]", "_", name)]
                _db["activity_seen"].create_index("t")
                logger.info(f"✅ MongoDB connected (db: {_db.name})")
    return _db


def _norm(path: str) -> str:
    return "/".join(p for p in str(path).strip("/").split("/") if p)


def _ancestors(path: str) -> list:
    parts = path.split("/")
    return ["/".join(parts[:i]) for i in range(1, len(parts))]


def _prefix_query(path: str) -> dict:
    return {"_id": {"$regex": "^" + re.escape(path + "/")}} if path else {}


class _Snapshot:
    def __init__(self, value):
        self._value = value

    def val(self):
        return self._value

    def each(self):
        if isinstance(self._value, dict):
            return [_Child(k, v) for k, v in self._value.items()]
        return None


class _Child:
    def __init__(self, key, value):
        self._k, self._v = key, value

    def key(self):
        return self._k

    def val(self):
        return self._v


class MongoTreeAdapter:
    """Pyrebase-like adapter over MongoDB path documents."""

    def __init__(self, path: str = "/", coll=None):
        self._path = _norm(path)
        self._coll = coll if coll is not None else database()["tree"]

    def child(self, *parts) -> "MongoTreeAdapter":
        extra = "/".join(_norm(p) for p in parts if _norm(p))
        return MongoTreeAdapter(f"{self._path}/{extra}" if self._path and extra else (self._path or extra), self._coll)

    # ---- helpers ----
    def _ancestor_doc(self):
        anc = _ancestors(self._path) if self._path else []
        if not anc:
            return None
        docs = list(self._coll.find({"_id": {"$in": anc}}))
        return max(docs, key=lambda d: len(d["_id"])) if docs else None

    def _read(self):
        path = self._path
        anc = self._ancestor_doc()
        if anc is not None:
            node = anc.get("v")
            for part in path[len(anc["_id"]) + 1:].split("/"):
                if not isinstance(node, dict) or part not in node:
                    return None
                node = node[part]
            return node
        exact = self._coll.find_one({"_id": path}) if path else None
        if exact is not None:
            return exact.get("v")
        out, found = {}, False
        for d in self._coll.find(_prefix_query(path)):
            rel = d["_id"][len(path) + 1:] if path else d["_id"]
            node = out
            keys = rel.split("/")
            for k in keys[:-1]:
                node = node.setdefault(k, {})
                if not isinstance(node, dict):
                    break
            else:
                node[keys[-1]] = d.get("v")
                found = True
        return out if found else None

    def _write(self, value, remove=False):
        path = self._path
        anc = self._ancestor_doc()
        if anc is not None:
            root = anc.get("v") if isinstance(anc.get("v"), dict) else {}
            keys = path[len(anc["_id"]) + 1:].split("/")
            node = root
            for k in keys[:-1]:
                if not isinstance(node.get(k), dict):
                    if remove:
                        return
                    node[k] = {}
                node = node[k]
            if remove:
                node.pop(keys[-1], None)
            else:
                node[keys[-1]] = value
            self._coll.replace_one({"_id": anc["_id"]}, {"_id": anc["_id"], "v": root})
            return
        if not path:
            if remove:
                self._coll.delete_many({})
                return
            if not isinstance(value, dict):
                raise ValueError("Root value must be a dict")
            for k, v in value.items():
                self.child(k).set(v)
            return
        self._coll.delete_many({"$or": [{"_id": path}, _prefix_query(path)]})
        if not remove:
            self._coll.insert_one({"_id": path, "v": value})

    # ---- pyrebase API ----
    def get(self):
        return _Snapshot(self._read())

    def set(self, data: Any):
        with _lock:
            self._write(data)

    def update(self, data: dict):
        with _lock:
            for k, v in (data or {}).items():
                self.child(k)._write(v)

    def remove(self):
        with _lock:
            self._write(None, remove=True)

    def push(self, data: Any):
        import time
        key = str(int(time.time() * 1000))
        self.child(key).set(data)
        return key

    def close(self):
        pass


def read_path(parts) -> Any:
    """Value at a path (for the code that used the in-memory dump.json cache)."""
    return MongoTreeAdapter("/".join(str(p) for p in parts)).get().val()


# ---------------- small settings ----------------
def kv_get(key, default=None):
    if key == "broadcasts":
        docs = list(database()["broadcasts"].find({}, sort=[("ts", 1)]))
        for d in docs:
            d["id"] = d.pop("_id")
        return docs or default
    doc = database()["ui_state"].find_one({"_id": key})
    return doc["v"] if doc else default


def kv_set(key, value) -> None:
    if key == "broadcasts":
        coll = database()["broadcasts"]
        keep = []
        for rec in value or []:
            rec = dict(rec)
            rid = rec.pop("id")
            keep.append(rid)
            coll.replace_one({"_id": rid}, {"_id": rid, **rec}, upsert=True)
        coll.delete_many({"_id": {"$nin": keep}})
        return
    database()["ui_state"].replace_one({"_id": key}, {"_id": key, "v": value}, upsert=True)


# ---------------- one-time import of the old files ----------------
def import_old_files(dump_file: str, state_file: str, activity_file: str = "") -> None:
    db = database()
    meta = db["meta"]
    if meta.find_one({"_id": "imported"}):
        return
    try:
        if os.path.exists(dump_file) and db["tree"].estimated_document_count() == 0:
            with open(dump_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            root = MongoTreeAdapter("/", db["tree"])
            bot = data.get("bot") if isinstance(data, dict) else None
            if isinstance(bot, dict):
                # users / blocked users one document each; other branches as they are
                for name, branch in bot.items():
                    if isinstance(branch, dict):
                        for sub, val in branch.items():
                            if sub in ("users", "blocked_users") and isinstance(val, dict):
                                for uid, u in val.items():
                                    root.child("bot", name, sub, uid).set(u)
                            else:
                                root.child("bot", name, sub).set(val)
                    else:
                        root.child("bot", name).set(branch)
                rest = {k: v for k, v in data.items() if k != "bot"}
            else:
                rest = data if isinstance(data, dict) else {}
            for k, v in rest.items():
                root.child(k).set(v)
            logger.info(f"✅ Imported {dump_file} into MongoDB")
        if os.path.exists(state_file) and db["ui_state"].estimated_document_count() == 0:
            with open(state_file, "r", encoding="utf-8") as f:
                state = json.load(f)
            for k, v in state.items():
                kv_set(k, v)
            logger.info(f"✅ Imported {state_file} into MongoDB")
        if activity_file and os.path.exists(activity_file) and db["activity_seen"].estimated_document_count() == 0:
            with open(activity_file, "r", encoding="utf-8") as f:
                act = json.load(f)
            seen = [{"_id": int(u), "t": int(t)} for u, t in (act.get("seen") or {}).items()]
            if seen:
                db["activity_seen"].insert_many(seen)
            for day, n in (act.get("dl") or {}).items():
                db["activity_dl"].replace_one({"_id": day}, {"_id": day, "n": n}, upsert=True)
            db["meta"].replace_one({"_id": "dl_total"}, {"_id": "dl_total", "n": act.get("dl_total", 0)}, upsert=True)
            if act.get("since"):
                db["meta"].replace_one({"_id": "activity_since"}, {"_id": "activity_since", "t": act["since"]}, upsert=True)
            logger.info(f"✅ Imported {activity_file} into MongoDB")
        meta.replace_one({"_id": "imported"}, {"_id": "imported", "ok": True}, upsert=True)
    except Exception as exc:
        logger.error(f"MongoDB import failed (old files left untouched): {exc}")
