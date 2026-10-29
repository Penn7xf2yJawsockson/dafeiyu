"""提示词组装(ADR-0003)。

    system:  [人设][核心档案]            ← 很少变化,可缓存
    history: [Kelivo 发来的历史]          ← 原样,只追加
    system:  <memory>[召回块]</memory>    ← 深度 1,每轮都变
    user:    [最新一条消息]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from gateway.stores import MemoryHit

PLACEHOLDER = "(本轮没有召回到相关记忆。)"
USAGE_RULES = "以下是与当前对话可能相关的记忆。只在相关时自然提及,不要逐条复述;事实冲突时以系统提示中的核心档案为准。"


@dataclass(frozen=True)
class Recall:
    """一轮的全部召回结果。各节按固定顺序渲染,节内按记忆 ID 排序。"""

    handoff: list[str] = field(default_factory=list)
    surfaced: list[MemoryHit] = field(default_factory=list)
    lore: list[MemoryHit] = field(default_factory=list)
    facts: list[MemoryHit] = field(default_factory=list)
    affect: list[MemoryHit] = field(default_factory=list)
    degraded_sources: list[str] = field(default_factory=list)

    def memory_ids(self) -> list[str]:
        sections = (("surfaced", self.surfaced), ("lore", self.lore), ("fact", self.facts), ("affect", self.affect))
        return [f"{name}:{hit.id}" for name, hits in sections for hit in hits]


_SECTIONS = (
    ("handoff", "刚才在另一台设备上的对话"),
    ("surfaced", "心里还挂着的事"),
    ("lore", "相关条目"),
    ("facts", "相关事实"),
    ("affect", "相关经历与感受"),
)


def render_recall_block(recall: Recall) -> str:
    parts: list[str] = []
    for attr, title in _SECTIONS:
        items = getattr(recall, attr)
        if not items:
            continue
        if attr == "handoff":
            lines = list(items)
        else:
            lines = [f"- {hit.text}" for hit in sorted(items, key=lambda h: h.id)]
        parts.append(f"## {title}\n" + "\n".join(lines))
    body = "\n\n".join(parts) if parts else PLACEHOLDER
    return f"<memory>\n{USAGE_RULES}\n\n{body}\n</memory>"


def _strip(message: dict[str, Any], notice: str) -> dict[str, Any]:
    content = message.get("content")
    if notice and message.get("role") == "assistant" and isinstance(content, str) and notice in content:
        return {**message, "content": content.replace(notice, "")}
    return message


def compose(system: str, history: list[dict[str, Any]], recall: Recall, notice: str = "") -> list[dict[str, Any]]:
    """history 的最后一条是本轮用户消息。notice 是降级标记,只给用户看,从历史里去掉后再发上游。"""
    *earlier, latest = [_strip(m, notice) for m in history]
    return [
        {"role": "system", "content": system},
        *earlier,
        {"role": "system", "content": render_recall_block(recall)},
        latest,
    ]
