"""记忆库接口。事实库(Hindsight)与情感库(Ombre-Brain)都实现同一份协议。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class MemoryHit:
    """一条被召回的记忆。id 用于审计,text 进入召回块。"""

    id: str
    text: str


class MemoryStore(Protocol):
    async def recall(self, query: str) -> list[MemoryHit]:
        """按当前话题检索。"""
        ...

    async def surface(self) -> list[MemoryHit]:
        """无查询的主动浮现(情感库在段的第一轮使用;事实库返回空)。"""
        ...

    async def write_episode(self, episode_id: str, transcript: str) -> None:
        """写入一段对话。同一 episode_id 重复写入必须是幂等的覆盖。"""
        ...
