"""Hindsight 召回参数(对照 Hindsight 官方文档 developer/retrieval 与 best-practices,2026-10-29)。

- prefer_observations:原始事实已被合并进返回的 observation 时不再重复返回,省出的位置往后补。
- budget=low:官方给聊天/高频调用的建议,每轮都召回,不需要多跳深挖。
- query_timestamp:用户本地当前时间,作为「下周」「昨天」这类相对时间和时近加分的基准。
- max_tokens=800:召回块只是参考,不必把预算用满。
"""

import json
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest

from gateway.adapters import HindsightStore


class _Clock:
    def now(self) -> datetime:
        return datetime(2026, 10, 29, 15, 28, 5, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_recall_asks_for_deduplicated_low_budget_results_anchored_at_local_time() -> None:
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"results": [{"id": "f1", "text": "用户下周六去南昌看演唱会"}]})

    store = HindsightStore("http://h", "dafeiyu", clock=_Clock(), tz="Asia/Shanghai",
                           transport=httpx.MockTransport(handler))
    hits = await store.recall("你还记得我下周有什么安排吗")

    assert [h.text for h in hits] == ["用户下周六去南昌看演唱会"]
    assert sent == [{"query": "你还记得我下周有什么安排吗", "max_tokens": 800, "budget": "low",
                     "prefer_observations": True, "query_timestamp": "2026-10-29T23:28:05+08:00"}]


async def test_list_and_bulk_state_calls() -> None:
    seen: list[tuple[str, str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body: Any = json.loads(request.content) if request.content else {}
        seen.append((request.method, str(request.url), body))
        if request.method == "GET":
            return httpx.Response(200, json={"items": [{"id": "m1", "text": "x"}], "total": 1})
        if request.url.path.endswith("/obs1"):
            return httpx.Response(400, json={"detail": "Memory 'obs1' is a observation; only world/experience facts can be curated."})
        return httpx.Response(200, json={"id": "m1", "state": body["state"]})

    store = HindsightStore("http://h", "dafeiyu", transport=httpx.MockTransport(handler))
    page = await store.list_memories(q="猫", fact_type="world", state="valid", limit=20, offset=40)
    assert page["total"] == 1
    method, url, _ = seen[0]
    assert method == "GET" and "/v1/default/banks/dafeiyu/memories/list" in url
    assert all(p in url for p in ("q=%E7%8C%AB", "type=world", "state=valid", "limit=20", "offset=40"))

    await store.set_memory_state("m1", "invalidated", reason="面板批量")
    assert seen[-1] == ("PATCH", "http://h/v1/default/banks/dafeiyu/memories/m1", {"state": "invalidated", "reason": "面板批量"})
    await store.set_memory_state("m1", "valid", reason="ignored")
    assert seen[-1][2] == {"state": "valid"}
    with pytest.raises(ValueError, match="observation"):
        await store.set_memory_state("obs1", "invalidated")
