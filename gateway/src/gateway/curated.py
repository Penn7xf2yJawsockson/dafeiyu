"""策划数据:人设、核心档案、网关世界书及其设置(工单 09)。每次变化都留一个版本,可以回滚。

文件仍然是 data/ 下的普通文件,可以直接用编辑器改;网关每次读取时比对内容,
发现被外部改过就补记一个版本(来源记为 external),所以不经过面板的修改也能回滚。

    data/persona.md            人设
    data/core_profile.md       核心档案
    data/lore_settings.json    世界书全局设置(LoreSettings 的字段 + disabled_worlds)
    data/worlds/<名字>.json     世界书,SillyTavern 格式
    data/curated.sqlite3       所有版本
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from dataclasses import fields
from datetime import datetime
from pathlib import Path
from typing import Any

from gateway.lorebook import LoreSettings, normalize_world

PERSONA, PROFILE, LORE_SETTINGS = "persona.md", "core_profile.md", "lore_settings.json"
_WORLD = re.compile(r"^worlds/([^/\\:*?\"<>|\x00-\x1f]{1,80})\.json$")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    at TEXT NOT NULL,
    source TEXT NOT NULL,
    content TEXT,          -- NULL 表示这一版是「删除」
    hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS versions_path ON versions (path, id);
"""


class CuratedError(ValueError):
    """面板传来的路径或内容不合法。"""


def world_path(name: str) -> str:
    path = f"worlds/{name}.json"
    if not _WORLD.match(path) or name.strip(". ") != name:
        raise CuratedError(f"世界书名不合法:{name!r}")
    return path


def _check_path(path: str) -> str:
    if path in (PERSONA, PROFILE, LORE_SETTINGS) or _WORLD.match(path):
        return path
    raise CuratedError(f"不支持的文件:{path!r}")


def _hash(content: str | None) -> str:
    return "deleted" if content is None else hashlib.sha256(content.encode()).hexdigest()


class CuratedStore:
    def __init__(self, data_dir: Path, clock: Any) -> None:
        self._dir, self._clock = data_dir, clock
        (data_dir / "worlds").mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(data_dir / "curated.sqlite3", check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript("PRAGMA journal_mode=WAL;" + _SCHEMA)

    # --- 读 ---

    def read(self, path: str) -> str | None:
        """文件当前内容(不存在为 None)。和最近一个版本不同时补记一个外部修改版本。"""
        path = _check_path(path)
        file = self._dir / path
        content = file.read_text(encoding="utf-8") if file.exists() else None
        self._record_if_changed(path, content, "external")
        return content

    def text(self, path: str) -> str:
        return (self.read(path) or "").strip()

    def world_names(self) -> list[str]:
        return sorted(p.stem for p in (self._dir / "worlds").glob("*.json") if _WORLD.match(f"worlds/{p.name}"))

    def worlds(self) -> dict[str, dict[str, Any]]:
        """启用中的世界书。读不了的文件(JSON 写坏了)跳过,不影响聊天。"""
        disabled = set(self.lore_raw().get("disabled_worlds") or [])
        out: dict[str, dict[str, Any]] = {}
        for name in self.world_names():
            if name in disabled:
                continue
            try:
                out[name] = normalize_world(json.loads(self.read(world_path(name)) or "{}"))
            except (ValueError, CuratedError):
                continue
        return out

    def lore_raw(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.read(LORE_SETTINGS) or "{}")
        except ValueError:
            return {}
        return raw if isinstance(raw, dict) else {}

    def lore_settings(self, defaults: LoreSettings) -> LoreSettings:
        raw = self.lore_raw()
        known = {f.name: f.type for f in fields(LoreSettings)}
        picked = {k: v for k, v in raw.items() if k in known and v is not None}
        try:
            return LoreSettings(**{**{f.name: getattr(defaults, f.name) for f in fields(LoreSettings)}, **picked})
        except TypeError:
            return defaults

    # --- 写 ---

    def write(self, path: str, content: str, source: str = "panel") -> int:
        path = _check_path(path)
        content = self._validate(path, content)
        self.read(path)  # 先给磁盘上的现状留一版(手写的原文件也能回滚回去)
        file = self._dir / path
        tmp = file.with_suffix(file.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(file)
        return self._record_if_changed(path, content, source) or self._last_id(path)

    def delete(self, path: str, source: str = "panel") -> int:
        path = _check_path(path)
        self.read(path)
        (self._dir / path).unlink(missing_ok=True)
        return self._record_if_changed(path, None, source) or self._last_id(path)

    def rollback(self, path: str, version_id: int) -> int:
        row = self._version_row(version_id)
        if row is None or row["path"] != _check_path(path):
            raise CuratedError("没有这个版本")
        if row["content"] is None:
            return self.delete(path, source=f"rollback:{version_id}")
        return self.write(path, row["content"], source=f"rollback:{version_id}")

    def _validate(self, path: str, content: str) -> str:
        if not path.endswith(".json"):
            return content
        try:
            data = json.loads(content)
        except ValueError as exc:
            raise CuratedError(f"JSON 格式错误:{exc}") from exc
        if not isinstance(data, dict):
            raise CuratedError("JSON 顶层必须是对象")
        if path.startswith("worlds/"):
            data = normalize_world(data)
        return json.dumps(data, ensure_ascii=False, indent=2)

    # --- 版本 ---

    def history(self, path: str) -> list[dict[str, Any]]:
        path = _check_path(path)
        with self._lock:
            rows = self._db.execute("SELECT id, at, source, hash, length(content) AS size FROM versions"
                                    " WHERE path = ? ORDER BY id DESC", (path,)).fetchall()
        return [{**dict(r), "deleted": r["hash"] == "deleted"} for r in rows]

    def version(self, version_id: int) -> dict[str, Any] | None:
        row = self._version_row(version_id)
        return dict(row) if row else None

    def _version_row(self, version_id: int) -> sqlite3.Row | None:
        with self._lock:
            row: sqlite3.Row | None = self._db.execute(
                "SELECT id, path, at, source, content FROM versions WHERE id = ?", (version_id,)).fetchone()
        return row

    def _last_id(self, path: str) -> int:
        with self._lock:
            row = self._db.execute("SELECT id FROM versions WHERE path = ? ORDER BY id DESC LIMIT 1",
                                   (path,)).fetchone()
        return int(row["id"]) if row else 0

    def _record_if_changed(self, path: str, content: str | None, source: str) -> int:
        """内容和最近一个版本不同才记一版,返回新版本 id;没变返回 0。文件第一次被看到时来源记为 initial。"""
        with self._lock:
            last = self._db.execute("SELECT hash FROM versions WHERE path = ? ORDER BY id DESC LIMIT 1",
                                    (path,)).fetchone()
        if last is None and content is None:
            return 0
        if last is not None and last["hash"] == _hash(content):
            return 0
        return self._record(path, content, source if last is not None or source != "external" else "initial")

    def _record(self, path: str, content: str | None, source: str) -> int:
        now: datetime = self._clock.now()
        with self._lock:
            cur = self._db.execute("INSERT INTO versions (path, at, source, content, hash) VALUES (?, ?, ?, ?, ?)",
                                   (path, now.isoformat(), source, content, _hash(content)))
        return int(cur.lastrowid or 0)
