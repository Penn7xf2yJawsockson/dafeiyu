"""轮状态机、段、写入队列。

上一轮怎么判定(收到本轮请求时):
- 本轮之前的历史 = 上一轮之前的历史(只是最后一条用户消息变了或没变)→ 重新生成/编辑,上一轮已取代
- 其他情况(接着聊下去,或在 Kelivo 里新开了对话)→ 上一轮没有被否定,确定
  (新开对话也按确定处理:用户 2026-10-29 确认)
只有最近一个待定轮会被取代;已确定的轮不回头改。重新生成失败时撤销取代。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from typing import Any

from gateway.config import Settings
from gateway.stores import MemoryHit, MemoryStore
from gateway.transcript import FAILED, PENDING, SETTLED, SUPERSEDED, TranscriptLog, Turn

FACTS, AFFECT = "facts", "affect"
_EDIT_SIMILARITY = 0.5


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

    def resolve_previous(self, device: str, companion: str, history: list[dict[str, Any]]) -> str | None:
        """history 不含系统消息,最后一条是本轮用户消息。返回被取代的那一轮的 id(若有)。

        对话开头(前面没有任何消息)时,「编辑第一句」和「新开一个对话」的历史长得一样;
        这时按文字相似度区分:几乎相同 → 编辑/重新生成;完全不同 → 新开对话。
        """
        prev = self.last_turn(device, companion)
        if prev is None or prev.status != PENDING:
            return None
        earlier = history[:-1]
        same_context = context_hash(earlier, self._notice) == prev.context_hash
        if same_context and not earlier:
            new_text = message_text(history[-1].get("content"))
            same_context = SequenceMatcher(None, prev.user_text, new_text).ratio() >= _EDIT_SIMILARITY
        if same_context:
            self._log.set_status(prev.id, SUPERSEDED)
            return prev.id
        self.settle(prev)  # 新开对话不等于否定上一轮
        return None

    def restore(self, turn_id: str) -> None:
        """重新生成失败时撤销取代:用户看到的仍是原来那版回复。"""
        self._log.set_status(turn_id, PENDING)

    def tool_round_of(self, device: str, companion: str, history: list[dict[str, Any]], start: int) -> Turn | None:
        """history 以工具结果结尾时,找出它所属的那一轮:这台设备最近的待定轮,
        且它开始的那条用户消息和之前的历史都对得上。找不到时返回 None,由调用方当作新的一轮。"""
        prev = self.last_turn(device, companion)
        if prev is None or prev.status != PENDING:
            return None
        if prev.user_text != message_text(history[start].get("content")):
            return None
        if prev.context_hash != context_hash(history[:start], self._notice):
            return None
        return prev

    def settle(self, turn: Turn) -> None:
        self._log.set_status(turn.id, SETTLED)
        self._log.enqueue(FACTS, turn.episode_id)

    def episode_for(self, device: str, companion: str, now: datetime) -> tuple[str, bool]:
        """返回 (episode_id, 是否为段的第一轮)。需要开新段时先关闭旧段。

        段里的轮全部被取代时(重新生成了段的第一轮),仍留在这一段,但按第一轮对待。
        """
        prev = self.last_turn(device, companion)
        if prev is not None and self._log.episode_closed_at(prev.episode_id) is None:
            idle = now - prev.created_at > timedelta(minutes=self._s.episode_idle_minutes)
            live = [t for t in self._log.turns("episode_id = ?", (prev.episode_id,))
                    if t.status in (PENDING, SETTLED)]
            if not idle and not live:
                return prev.episode_id, True
            if not idle and len(live) < self._s.episode_max_turns:
                return prev.episode_id, False
            self.close_episode(prev.episode_id, now)
        return self._log.open_episode(device=device, companion=companion, started_at=now), True

    def handoff(self, device: str, companion: str, now: datetime) -> list[MemoryHit]:
        """原文日志里最近一段若来自另一台设备、且最后一轮在窗口内,返回那一段最后几轮(ADR-0004)。

        必须在本轮写入原文日志之前调用。
        """
        turns = self._log.turns("companion = ? AND status IN (?, ?)", (companion, PENDING, SETTLED))
        if not turns:
            return []
        latest = turns[-1]
        if latest.device == device or now - latest.created_at > timedelta(hours=self._s.handoff_window_hours):
            return []
        episode = [t for t in turns if t.episode_id == latest.episode_id][-self._s.handoff_turns:]
        return [MemoryHit(t.id, render_transcript([t])) for t in episode]

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
