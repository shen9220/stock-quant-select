"""JSON 持久化：涨停池 / 观察池 / 持有池。

V1 → data/ ；V2 → data_v2/ 。模块级函数默认仍走 V1，保证旧代码零改动。
"""
from __future__ import annotations

import json
import os
import shutil
import threading
from copy import deepcopy
from datetime import datetime
from typing import Any, Callable

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DATA_DIR_V2 = os.path.join(BASE_DIR, "data_v2")
_LEGACY_OBS = os.path.join(DATA_DIR, "observation.json")

_global_lock = threading.Lock()
_STORES: dict[str, "JsonStore"] = {}


def _read(path: str, default: Any):
    if not os.path.exists(path):
        return deepcopy(default)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return deepcopy(default)


def _write(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    os.replace(tmp, path)


def _code_lup_key(rec: dict) -> tuple:
    return (str(rec.get("stock_code") or "").zfill(6), str(rec.get("limit_up_date") or ""))


class JsonStore:
    """按目录隔离的三池存储。"""

    def __init__(self, data_dir: str, profile: str = "v1"):
        self.profile = profile
        self.data_dir = data_dir
        self.limitup_path = os.path.join(data_dir, "limitup.json")
        self.watch_path = os.path.join(data_dir, "watch.json")
        self.pos_path = os.path.join(data_dir, "position.json")
        self.meta_path = os.path.join(data_dir, "meta.json")
        self._lock = threading.Lock()
        os.makedirs(data_dir, exist_ok=True)
        if profile == "v1":
            self._migrate_legacy()
        # 标记 profile
        meta = self.meta_get()
        if meta.get("profile") != profile:
            self.meta_set({"profile": profile, "strategy_version": profile.upper()})

    def _migrate_legacy(self) -> None:
        if os.path.exists(self.limitup_path):
            return
        if os.path.exists(_LEGACY_OBS):
            shutil.copy2(_LEGACY_OBS, self.limitup_path)

    def _pool_load(self, path: str) -> list[dict]:
        with self._lock:
            data = _read(path, {"items": [], "next_id": 1})
            return data.get("items", [])

    def _pool_upsert(self, path: str, rec: dict, key_fn: Callable[[dict], tuple]) -> dict:
        with self._lock:
            cur = _read(path, {"items": [], "next_id": 1})
            items = cur.get("items", [])
            key = key_fn(rec)
            for i, old in enumerate(items):
                if key_fn(old) == key:
                    merged = {**old, **rec, "id": old.get("id")}
                    items[i] = merged
                    _write(path, {"items": items, "next_id": cur.get("next_id", 1)})
                    return merged
            nid = int(cur.get("next_id") or 1)
            rec = {**rec, "id": nid}
            items.append(rec)
            _write(path, {"items": items, "next_id": nid + 1})
            return rec

    def _pool_get(self, path: str, oid: int) -> dict | None:
        for x in self._pool_load(path):
            if int(x.get("id") or 0) == int(oid):
                return x
        return None

    def _pool_delete(self, path: str, oid: int) -> bool:
        with self._lock:
            cur = _read(path, {"items": [], "next_id": 1})
            items = cur.get("items", [])
            n = len(items)
            items = [x for x in items if int(x.get("id") or 0) != int(oid)]
            if len(items) == n:
                return False
            _write(path, {"items": items, "next_id": cur.get("next_id", 1)})
            return True

    def _pool_update(self, path: str, oid: int, patch: dict) -> dict | None:
        with self._lock:
            cur = _read(path, {"items": [], "next_id": 1})
            items = cur.get("items", [])
            for i, old in enumerate(items):
                if int(old.get("id") or 0) == int(oid):
                    merged = {**old, **patch, "id": old.get("id")}
                    items[i] = merged
                    _write(path, {"items": items, "next_id": cur.get("next_id", 1)})
                    return merged
            return None

    # ── 涨停池 ──
    def load_limitups(self) -> list[dict]:
        return self._pool_load(self.limitup_path)

    def upsert_limitup(self, rec: dict) -> dict:
        return self._pool_upsert(self.limitup_path, rec, _code_lup_key)

    def get_limitup(self, oid: int) -> dict | None:
        return self._pool_get(self.limitup_path, oid)

    def delete_limitup(self, oid: int) -> bool:
        return self._pool_delete(self.limitup_path, oid)

    def update_limitup(self, oid: int, patch: dict) -> dict | None:
        return self._pool_update(self.limitup_path, oid, patch)

    def limitup_dates(self) -> list[str]:
        return sorted(
            {str(x.get("limit_up_date")) for x in self.load_limitups() if x.get("limit_up_date")},
            reverse=True,
        )

    # ── 观察池 ──
    def load_watches(self) -> list[dict]:
        return self._pool_load(self.watch_path)

    def upsert_watch(self, rec: dict) -> dict:
        return self._pool_upsert(self.watch_path, rec, _code_lup_key)

    def get_watch(self, oid: int) -> dict | None:
        return self._pool_get(self.watch_path, oid)

    def delete_watch(self, oid: int) -> bool:
        return self._pool_delete(self.watch_path, oid)

    def update_watch(self, oid: int, patch: dict) -> dict | None:
        return self._pool_update(self.watch_path, oid, patch)

    def find_watch(self, code: str, limit_up_date: str) -> dict | None:
        code = str(code).zfill(6)
        for x in self.load_watches():
            if str(x.get("stock_code") or "").zfill(6) == code and x.get("limit_up_date") == limit_up_date:
                return x
        return None

    # ── 持有池 ──
    def load_positions(self) -> list[dict]:
        return self._pool_load(self.pos_path)

    def upsert_position(self, rec: dict) -> dict:
        with self._lock:
            cur = _read(self.pos_path, {"items": [], "next_id": 1})
            items = cur.get("items", [])
            if rec.get("id") is not None:
                for i, old in enumerate(items):
                    if int(old.get("id") or 0) == int(rec["id"]):
                        merged = {**old, **rec, "id": old.get("id")}
                        items[i] = merged
                        _write(self.pos_path, {"items": items, "next_id": cur.get("next_id", 1)})
                        return merged
            nid = int(cur.get("next_id") or 1)
            rec = {**rec, "id": nid}
            items.append(rec)
            _write(self.pos_path, {"items": items, "next_id": nid + 1})
            return rec

    def get_position(self, pid: int) -> dict | None:
        return self._pool_get(self.pos_path, pid)

    def delete_position(self, pid: int) -> bool:
        return self._pool_delete(self.pos_path, pid)

    def update_position(self, pid: int, patch: dict) -> dict | None:
        return self._pool_update(self.pos_path, pid, patch)

    def holding_count(self) -> int:
        return sum(1 for x in self.load_positions() if x.get("status") == "holding")

    def meta_get(self) -> dict:
        with self._lock:
            return _read(self.meta_path, {})

    def meta_set(self, patch: dict) -> dict:
        with self._lock:
            cur = _read(self.meta_path, {})
            cur.update(patch)
            cur["updated_at"] = datetime.now().isoformat(timespec="seconds")
            _write(self.meta_path, cur)
            return cur


def for_profile(profile: str | None = "v1") -> JsonStore:
    p = (profile or "v1").strip().lower()
    if p not in ("v1", "v2"):
        p = "v1"
    with _global_lock:
        if p not in _STORES:
            d = DATA_DIR if p == "v1" else DATA_DIR_V2
            _STORES[p] = JsonStore(d, p)
        return _STORES[p]


# ── V1 模块级别名（旧代码零改动）──────────────────────────
_v1 = for_profile("v1")

LIMITUP_PATH = _v1.limitup_path
WATCH_PATH = _v1.watch_path
POS_PATH = _v1.pos_path
META_PATH = _v1.meta_path

load_limitups = _v1.load_limitups
upsert_limitup = _v1.upsert_limitup
get_limitup = _v1.get_limitup
delete_limitup = _v1.delete_limitup
update_limitup = _v1.update_limitup
limitup_dates = _v1.limitup_dates

load_watches = _v1.load_watches
upsert_watch = _v1.upsert_watch
get_watch = _v1.get_watch
delete_watch = _v1.delete_watch
update_watch = _v1.update_watch
find_watch = _v1.find_watch

load_observations = load_limitups
upsert_observation = upsert_limitup
get_observation = get_limitup
delete_observation = delete_limitup
update_observation = update_limitup

load_positions = _v1.load_positions
upsert_position = _v1.upsert_position
get_position = _v1.get_position
delete_position = _v1.delete_position
update_position = _v1.update_position
holding_count = _v1.holding_count

meta_get = _v1.meta_get
meta_set = _v1.meta_set
