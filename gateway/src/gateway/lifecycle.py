"""轮状态机、段、写入队列。

上一轮怎么判定(收到本轮请求时):
- Kelivo 发来的历史 = 上一轮的历史 + 上一轮的用户消息 + 上一轮的回复 → 上一轮确定
- Kelivo 发来的历史 = 上一轮的历史(只是最后一条用户消息变了或没变)→ 重新生成/编辑,上一轮已取代
- 都不是(新开了对话)→ 上一轮没有被否定,确定
只有最近一个待定轮会被取代;已确定的轮不回头改。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any

from gateway.config import Settings
from gateway.stores import MemoryStore
from gateway.transcript import FAILED, PENDING, SETTLED, SUPERSEDED, TranscriptLog, Turn

FACTS, AFFECT = "facts", "affect"


def message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return ""


def context_hash(messages: list[dict[str, Any]], strip: str = "") -> str:
    """只看角色与文本,和 Kelivo 把内容发成字符串还是分段数组无关。"""
    norm = []
    for m in messages:
        text = message_text(m.get("content")).strip()
        if strip:
            text = text.replace(strip, "").strip()
        norm.append([m.get("role"), text])
    return hashlib.sha256(json.dumps(norm, ensure_ascii=False).encode()).hexdigest()


def render_transcript(turns: list[Turn], user_name: str = "用户", ai_name: str = "大肥鱼") -> str:
    return "\n".join(f"{user_name}:{t.user_text}\n{ai_name}:{t.assistant_text}" for t in turns)


class Lifecycle:
    def __init__(self, settings: Settings, log: TranscriptLog, facts: MemoryStore, affect: MemoryStore,
                 notice: str) -> None:
        self._s, self._log, self._facts, self._affect, self._notice = settings, log, facts, affect, notice

    def last_turn(self, device: str, companion: str) -> Turn | None:
        turns = self._log.turns("device = ? AND companion = ? AND status != ?", (device, companion, FAILED))
        return turns[-1] if turns else None

    def resolve_previous(self, device: str, companion: str, history: list[dict[str, Any]]) -> None:
        """history 不含系统消息,最后一条是本轮用户消息。"""
        prev = self.last_turn(device, companion)
        if prev is None or prev.status != PENDING:
            return
        earlier = history[:-1]
        if context_hash(earlier, self._notice) == prev.context_hash:
            self._log.set_status(prev.id, SUPERSEDED)
        else:
            self.settle(prev)

    def settle(self, turn: Turn) -> None:
        self._log.set_status(turn.id, SETTLED)
        self._log.enqueue(FACTS, turn.episode_id)

    def episode_for(self, device: str, companion: str, now: datetime) -> tuple[str, bool]:
        """返回 (episode_id, 是否为段的第一轮)。需要开新段时先关闭旧段。"""
        prev = self.last_turn(device, companion)
        if prev is not None and self._log.episode_closed_at(prev.episode_id) is None:
            idle = now - prev.created_at > timedelta(minutes=self._s.episode_idle_minutes)
            live = [t for t in self._log.turns("episode_id = ? AND status != ?", (prev.episode_id, SUPERSEDED))
                    if t.status != FAILED]
            if not idle and len(live) < self._s.episode_max_turns:
                return prev.episode_id, False
            self.close_episode(prev.episode_id, now)
        return self._log.open_episode(device=device, companion=companion, started_at=now), True

    def handoff(self, device: str, companion: str, now: datetime) -> list[str]:
        """另一台设备上最近一段,若其最后一轮在窗口内,返回那一段最后几轮的原文。"""
        others = self._log.turns("device != ? AND companion = ? AND status IN (?, ?)",
                                 (device, companion, PENDING, SETTLED))
        if not others:
            return []
        latest = others[-1]
        if now - latest.created_at > timedelta(hours=self._s.handoff_window_hours):
            return []
        episode = [t for t in others if t.episode_id == latest.episode_id][-self._s.handoff_turns:]
        return [f"用户:{t.user_text}\n大肥鱼:{t.assistant_text}" for t in episode]

    def close_episode(self, episode_id: str, now: datetime) -> None:
        for t in self._log.turns("episode_id = ? AND status = ?", (episode_id, PENDING)):
            self.settle(t)
        if self._log.close_episode(episode_id, now):
            self._log.enqueue(AFFECT, episode_id)

    def sweep_idle(self, now: datetime) -> None:
        cutoff = timedelta(minutes=self._s.episode_idle_minutes)
        for episode_id, _device, _companion in self._log.open_episodes():
            turns = [t for t in self._log.turns("episode_id = ?", (episode_id,)) if t.status != FAILED]
            if turns and now - turns[-1].created_at > cutoff:
                self.close_episode(episode_id, now)

    async def drain(self) -> None:
        for seq, kind, episode_id in self._log.pending_jobs():
            settled = self._log.turns("episode_id = ? AND status = ?", (episode_id, SETTLED))
            store = self._facts if kind == FACTS else self._affect
            try:
                if settled:
                    await store.write_episode(episode_id, render_transcript(settled))
            except Exception as exc:  # 写入失败留在队列里,下次重试
                self._log.finish_job(seq, f"{type(exc).__name__}: {exc}")
                continue
            self._log.finish_job(seq, None)
