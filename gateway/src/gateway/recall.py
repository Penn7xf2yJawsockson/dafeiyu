"""召回编排:并发调用各来源,总超时内没返回或出错的来源跳过,并记为降级。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable

from gateway.compose import Recall
from gateway.stores import MemoryHit, MemoryStore


async def _guarded(name: str, call: Awaitable[list[MemoryHit]], timeout: float,
                   degraded: list[str]) -> list[MemoryHit]:
    try:
        return await asyncio.wait_for(call, timeout)
    except Exception:  # 任何来源的故障都只让本轮降级,不中断回复
        degraded.append(name)
        return []


async def recall(*, query: str, facts: MemoryStore, affect: MemoryStore, timeout: float,
                 surface: bool, handoff: list[MemoryHit], lore: list[MemoryHit]) -> Recall:
    degraded: list[str] = []
    jobs = [
        _guarded("facts", facts.recall(query), timeout, degraded),
        _guarded("affect", affect.recall(query), timeout, degraded),
    ]
    if surface:
        jobs.append(_guarded("surfaced", affect.surface(), timeout, degraded))
    results = await asyncio.gather(*jobs)
    return Recall(
        handoff=handoff,
        surfaced=results[2] if surface else [],
        lore=lore,
        facts=results[0],
        affect=results[1],
        degraded_sources=sorted(degraded),
    )
