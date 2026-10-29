"""原文日志与审计(ADR-0001:原文日志是唯一真源)。

轮的正文只追加、不改写;变化的只有状态(待定 → 确定 / 已取代 / 失败)。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

PENDING, SETTLED, SUPERSEDED, FAILED = "pending", "settled", "superseded", "failed"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT UNIQUE NOT NULL,
    device TEXT NOT NULL,
    companion TEXT NOT NULL,
    episode_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    context_hash TEXT NOT NULL,
    user_text TEXT NOT NULL,
    assistant_text TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    recall_block TEXT NOT NULL DEFAULT '',
    recall_reasons TEXT NOT NULL DEFAULT '{}',
    recall_ids TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS episodes (
    id TEXT PRIMARY KEY,
    device TEXT NOT NULL,
    companion TEXT NOT NULL,
    started_at TEXT NOT NULL,
    closed_at TEXT
);
CREATE TABLE IF NOT EXISTS outbox (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    episode_id TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    done INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS audit (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_id TEXT NOT NULL,
    device TEXT NOT NULL,
    created_at TEXT NOT NULL,
    outcome TEXT NOT NULL,
    upstream TEXT NOT NULL,
    degraded_sources TEXT NOT NULL,
    degraded_reasons TEXT NOT NULL DEFAULT '{}',
    memory_ids TEXT NOT NULL,
    latency_ms INTEGER NOT NULL,
    tool_calls TEXT NOT NULL DEFAULT '[]'
);
"""


@dataclass(frozen=True)
class Turn:
    id: str
    device: str
    companion: str
    episode_id: str
    created_at: datetime
    context_hash: str
    user_text: str
    assistant_text: str
    status: str


class TranscriptLog:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript("PRAGMA journal_mode=WAL;" + _SCHEMA)
            self._migrate()

    def _migrate(self) -> None:
        """旧数据库补列。只加列,不改不删,已有数据原样保留。"""
        added = {
            "audit": [("degraded_reasons", "TEXT NOT NULL DEFAULT '{}'"), ("tool_calls", "TEXT NOT NULL DEFAULT '[]'")],
            "turns": [("recall_block", "TEXT NOT NULL DEFAULT ''"), ("recall_reasons", "TEXT NOT NULL DEFAULT '{}'"),
                      ("recall_ids", "TEXT NOT NULL DEFAULT '[]'")],
        }
        for table, wanted in added.items():
            columns = {r["name"] for r in self._db.execute(f"PRAGMA table_info({table})")}
            for name, decl in wanted:
                if name not in columns:
                    self._db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

    def open_turn(self, *, device: str, companion: str, episode_id: str, created_at: datetime,
                  context_hash: str, user_text: str) -> Turn:
        turn = Turn(uuid.uuid4().hex, device, companion, episode_id, created_at, context_hash, user_text, "", PENDING)
        with self._lock:
            self._db.execute(
                "INSERT INTO turns (id, device, companion, episode_id, created_at, context_hash, user_text, status)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (turn.id, device, companion, episode_id, created_at.isoformat(), context_hash, user_text, PENDING),
            )
        return turn

    def complete_turn(self, turn_id: str, assistant_text: str) -> None:
        with self._lock:
            self._db.execute("UPDATE turns SET assistant_text = ? WHERE id = ?", (assistant_text, turn_id))

    def save_recall(self, turn_id: str, block: str, reasons: dict[str, str], memory_ids: list[str]) -> None:
        """一轮的召回块只算一次;同一轮后续的工具回合原样复用,保证缓存前缀不变。"""
        with self._lock:
            self._db.execute("UPDATE turns SET recall_block = ?, recall_reasons = ?, recall_ids = ? WHERE id = ?",
                             (block, json.dumps(reasons, ensure_ascii=False), json.dumps(memory_ids), turn_id))

    def saved_recall(self, turn_id: str) -> tuple[str, dict[str, str], list[str]]:
        with self._lock:
            row = self._db.execute("SELECT recall_block, recall_reasons, recall_ids FROM turns WHERE id = ?",
                                   (turn_id,)).fetchone()
        return row["recall_block"], json.loads(row["recall_reasons"]), json.loads(row["recall_ids"])

    def set_status(self, turn_id: str, status: str) -> None:
        with self._lock:
            self._db.execute("UPDATE turns SET status = ? WHERE id = ?", (status, turn_id))

    def turns(self, where: str = "1=1", params: tuple[Any, ...] = ()) -> list[Turn]:
        with self._lock:
            rows = self._db.execute(f"SELECT * FROM turns WHERE {where} ORDER BY seq", params).fetchall()
        return [_turn(r) for r in rows]

    # --- 段 ---

    def open_episode(self, *, device: str, companion: str, started_at: datetime) -> str:
        episode_id = uuid.uuid4().hex
        with self._lock:
            self._db.execute("INSERT INTO episodes (id, device, companion, started_at) VALUES (?, ?, ?, ?)",
                             (episode_id, device, companion, started_at.isoformat()))
        return episode_id

    def close_episode(self, episode_id: str, closed_at: datetime) -> bool:
        """返回 True 表示这次调用真正关闭了它(避免重复关闭、重复写入情感库)。"""
        with self._lock:
            cur = self._db.execute("UPDATE episodes SET closed_at = ? WHERE id = ? AND closed_at IS NULL",
                                   (closed_at.isoformat(), episode_id))
        return cur.rowcount == 1

    def open_episodes(self) -> list[tuple[str, str, str]]:
        """(episode_id, device, companion),尚未关闭的段。"""
        with self._lock:
            rows = self._db.execute("SELECT id, device, companion FROM episodes WHERE closed_at IS NULL").fetchall()
        return [(r["id"], r["device"], r["companion"]) for r in rows]

    def episode_closed_at(self, episode_id: str) -> datetime | None:
        with self._lock:
            row = self._db.execute("SELECT closed_at FROM episodes WHERE id = ?", (episode_id,)).fetchone()
        return datetime.fromisoformat(row["closed_at"]) if row and row["closed_at"] else None

    # --- 写入队列(持久保证:任务先落盘,失败重试,重启后续跑) ---

    def enqueue(self, kind: str, episode_id: str) -> None:
        with self._lock:
            pending = self._db.execute("SELECT 1 FROM outbox WHERE kind = ? AND episode_id = ? AND done = 0",
                                       (kind, episode_id)).fetchone()
            if pending is None:
                self._db.execute("INSERT INTO outbox (kind, episode_id) VALUES (?, ?)", (kind, episode_id))

    def pending_jobs(self) -> list[tuple[int, str, str]]:
        with self._lock:
            rows = self._db.execute("SELECT seq, kind, episode_id FROM outbox WHERE done = 0 ORDER BY seq").fetchall()
        return [(r["seq"], r["kind"], r["episode_id"]) for r in rows]

    def finish_job(self, seq: int, error: str | None) -> None:
        with self._lock:
            if error is None:
                self._db.execute("UPDATE outbox SET done = 1, attempts = attempts + 1 WHERE seq = ?", (seq,))
            else:
                self._db.execute("UPDATE outbox SET attempts = attempts + 1, last_error = ? WHERE seq = ?",
                                 (error, seq))

    # --- 审计 ---

    def record_audit(self, *, turn_id: str, device: str, created_at: datetime, outcome: str, upstream: str,
                     degraded_reasons: dict[str, str], memory_ids: list[str], latency_ms: int,
                     tool_calls: list[str] | None = None) -> None:
        """tool_calls 只记工具名;工具参数和结果不进审计,也不进记忆。"""
        with self._lock:
            self._db.execute(
                "INSERT INTO audit (turn_id, device, created_at, outcome, upstream, degraded_sources,"
                " degraded_reasons, memory_ids, latency_ms, tool_calls) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (turn_id, device, created_at.isoformat(), outcome, upstream, json.dumps(sorted(degraded_reasons)),
                 json.dumps(degraded_reasons, ensure_ascii=False), json.dumps(memory_ids), latency_ms,
                 json.dumps(tool_calls or [])),
            )

    def audit_entries(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM audit ORDER BY seq").fetchall()
        out = []
        for r in rows:
            entry = dict(r)
            entry["degraded_sources"] = json.loads(entry["degraded_sources"])
            entry["degraded_reasons"] = json.loads(entry["degraded_reasons"])
            entry["tool_calls"] = json.loads(entry["tool_calls"])
            entry["memory_ids"] = json.loads(entry["memory_ids"])
            entry.pop("seq")
            out.append(entry)
        return out


def _turn(r: sqlite3.Row) -> Turn:
    return Turn(r["id"], r["device"], r["companion"], r["episode_id"], datetime.fromisoformat(r["created_at"]),
                r["context_hash"], r["user_text"], r["assistant_text"], r["status"])
