"""提示词组装(ADR-0003)。发给上游的消息从上到下:

    system  人设 / 世界书↑角色定义 / 核心档案 / 世界书↓角色定义(空一行拼成一条) ← 很少变化,可缓存
    system  客户端自己的系统消息(Kelivo / DSH)
    ……      历史(原样);世界书 @D 条目按深度插在其中
    system  <memory>召回块</memory>                 ← 深度 1,每轮都变
    user    本轮用户这句话                          ← 深度 0
    ……      DSH 注入的上下文、工具回合
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from gateway.stores import MemoryHit

PLACEHOLDER = "(本轮没有召回到相关记忆。)"
USAGE_RULES = "以下是与当前对话可能相关的记忆。只在相关时自然提及,不要逐条复述;事实冲突时以系统提示中的核心档案为准。"


@dataclass(frozen=True)
class Recall:
    """一轮的全部召回结果。各节按固定顺序渲染,节内按记忆 ID 排序。"""

    handoff: list[MemoryHit] = field(default_factory=list)
    facts: list[MemoryHit] = field(default_factory=list)
    surfaced: list[MemoryHit] = field(default_factory=list)
    affect: list[MemoryHit] = field(default_factory=list)
    degraded_reasons: dict[str, str] = field(default_factory=dict)
    """降级的来源 → 原因(超时或报错)。为空表示本轮没有降级。"""

    @property
    def degraded_sources(self) -> list[str]:
        return sorted(self.degraded_reasons)

    def memory_ids(self) -> list[str]:
        return [f"{source}:{hit.id}" for source, _title, _ordered in _SECTIONS for hit in getattr(self, source)]


# (来源, 标题, 是否保持原有顺序)。顺序固定:跨设备衔接 → 事实库 → 情感库(ADR-0003)。
# 网关世界书不在召回块里,按条目自己的注入位置放(lorebook.place)。
# 跨设备衔接保持对话的时间顺序;其余按记忆 ID 排序,保证同样的召回结果每次渲染一致。
_SECTIONS = (
    ("handoff", "刚才在另一台设备上的对话", True),
    ("facts", "相关事实", False),
    ("surfaced", "心里还挂着的事", False),
    ("affect", "相关经历与感受", False),
)


_WEEKDAYS = "一二三四五六日"


def local_time_line(now: datetime, tz: str) -> str:
    """模型不知道今天几号;这一行放在召回块里(每轮本来就变),不影响缓存前缀。"""
    local = now.astimezone(ZoneInfo(tz))
    return f"现在是 {local:%Y-%m-%d} 周{_WEEKDAYS[local.weekday()]} {local:%H:%M}。"


def render_recall_block(recall: Recall, time_line: str = "") -> str:
    parts: list[str] = []
    for source, title, ordered in _SECTIONS:
        hits: list[MemoryHit] = getattr(recall, source)
        if hits:
            shown = hits if ordered else sorted(hits, key=lambda h: h.id)
            parts.append(f"## {title}\n" + "\n".join(f"- {h.text}" for h in shown))
    body = "\n\n".join(parts) if parts else PLACEHOLDER
    head = f"{time_line}\n{USAGE_RULES}" if time_line else USAGE_RULES
    return f"<memory>\n{head}\n\n{body}\n</memory>"


def _strip(message: dict[str, Any], notice: str) -> dict[str, Any]:
    content = message.get("content")
    if not notice or message.get("role") != "assistant":
        return message
    if isinstance(content, str):
        return {**message, "content": content.replace(notice.strip(), "").rstrip()}
    if isinstance(content, list):
        parts = [{**p, "text": p["text"].replace(notice.strip(), "").rstrip()}
                 if isinstance(p, dict) and p.get("type") == "text" else p for p in content]
        return {**message, "content": parts}
    return message


def turn_start(history: list[dict[str, Any]]) -> int | None:
    """本轮开始的那条用户消息的下标。带工具的一轮里,它后面还跟着模型的工具调用和工具结果。"""
    for i in range(len(history) - 1, -1, -1):
        if history[i].get("role") == "user":
            return i
    return None


def system_head(persona: str, profile: str, before: list[str], after: list[str]) -> str:
    """第一条 system:人设、世界书↑角色定义、核心档案、世界书↓角色定义,空一行拼起来;没有世界书命中时就是人设+核心档案。"""
    return "\n\n".join(p for p in (persona, "\n".join(before), profile, "\n".join(after)) if p)


def compose(segments: list[str], history: list[dict[str, Any]], block: str, start: int, notice: str = "",
            depth: dict[tuple[int, int], str] | None = None) -> list[dict[str, Any]]:
    """segments:系统提示的各段,按顺序各成一条 system 消息(空的不发)。

    - 召回块插在本轮开始的那条用户消息之前(ADR-0003 的深度 1);带工具的一轮,后续请求里
      召回块仍在同一位置、内容不变,工具结果紧跟工具调用(协议要求),缓存前缀也不动。
    - depth:世界书 @D 条目,键 (深度, 角色)。深度从本轮用户这句话往前数:0 = 紧跟在它后面,
      1 = 紧挨在它前面(在召回块之前),以此类推;超过历史长度就放在历史最前面。同一位置的
      多条按酒馆的顺序排(深的在前;同深度 AI、用户、系统)。不会插进工具调用和工具结果之间。
    - notice 是降级标记,只给用户看,从历史里去掉后再发上游。
    """
    cleaned = [_strip(m, notice) for m in history]
    head = [{"role": "system", "content": text} for text in segments if text]
    body = [*cleaned[:start], {"role": "system", "content": block}, *cleaned[start:]]
    inserts: list[tuple[int, int, int, str]] = []
    for (d, role), text in (depth or {}).items():
        # body 里召回块在 start、用户这句话在 start+1。深度 ≥1 插在用户这句话(连同贴着它的召回块)之前第 d-1 条
        # 历史的前面;深度 0 插在用户这句话后面、DSH 注入和工具回合之前。
        index = start + 2 if d == 0 else max(0, start + 1 - d)
        while 0 < index < len(body) and body[index].get("role") == "tool":
            index -= 1  # 不拆开工具调用和它的结果
        inserts.append((index, -d, _ROLE_ORDER.get(role, 2), text))
    out = list(body)
    for index, _d, rank, text in sorted(inserts, reverse=True):
        out.insert(index, {"role": _ROLE_NAMES[rank], "content": text})
    return [*head, *out]


_ROLE_ORDER = {2: 0, 1: 1, 0: 2}         # 同一位置:AI、用户、系统(与酒馆 doChatInject 一致)
_ROLE_NAMES = {0: "assistant", 1: "user", 2: "system"}
