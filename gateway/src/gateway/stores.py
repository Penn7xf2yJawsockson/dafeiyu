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
        """写入一段对话。写入队列会在失败后重试,实现必须保证重试不产生重复记忆:

        - 事实库(Hindsight):同一 document_id 再次 retain 即整体替换,天然幂等。
        - 情感库(Ombre-Brain):grow 对相同内容有 30 分钟进程内去重(INTERNALS §3.3);
          网关渲染的段原文是确定的,所以窗口内的重试安全。Ombre 重启或超过窗口后的重试
          仍可能重复——这是已知限制,见契约测试。
        """
        ...
