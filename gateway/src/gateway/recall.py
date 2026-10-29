"""召回编排:并发调用各来源,总超时内没返回或出错的来源跳过,并记为降级(连同原因,便于审计)。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable

from gateway.compose import Recall
from gateway.stores import MemoryHit, MemoryStore

logger = logging.getLogger("gateway.recall")
_REASON_LIMIT = 300


async def _guarded(name: str, call: Awaitable[list[MemoryHit]], timeout: float,
                   reasons: dict[str, str]) -> list[MemoryHit]:
    try:
        return await asyncio.wait_for(call, timeout)
    except TimeoutError:
        reasons[name] = f"timeout after {timeout:g}s"
    except Exception as exc:  # 任何来源的故障都只让本轮降级,不中断回复
        reasons[name] = f"{type(exc).__name__}: {exc}"[:_REASON_LIMIT]
    logger.warning("召回降级 source=%s reason=%s", name, reasons[name])
    return []


async def recall(*, query: str, facts: MemoryStore, affect: MemoryStore, timeout: float,
                 surface: bool, handoff: list[MemoryHit]) -> Recall:
    reasons: dict[str, str] = {}
    jobs = [
        _guarded("facts", facts.recall(query), timeout, reasons),
        _guarded("affect", affect.recall(query), timeout, reasons),
    ]
    if surface:
        jobs.append(_guarded("surfaced", affect.surface(), timeout, reasons))
    results = await asyncio.gather(*jobs)
    surfaced = results[2] if surface else []
    # 同一个桶既被段首浮现、又被本轮检索命中时,只留在「心里还挂着的事」里
    seen = {h.id for h in surfaced}
    return Recall(
        handoff=handoff,
        surfaced=surfaced,
        facts=results[0],
        affect=[h for h in results[1] if h.id not in seen],
        degraded_reasons=dict(sorted(reasons.items())),
    )
