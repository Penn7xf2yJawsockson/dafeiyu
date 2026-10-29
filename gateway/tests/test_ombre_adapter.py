"""Ombre-Brain 返回文本的解析(格式取自 Ombre 3.6.14 的真实返回,2026-10-29)。

带查询的 breath_search / breath_advanced 会在末尾附 `=== ombre:result-ids ===` 块;
不带查询的浮现(breath_advanced 无 query)不附这个块,ID 只出现在正文的 `[bucket_id:…]` 里。
之前只认 ID 块,段首浮现因此一直是空的。
"""

import json
from typing import Any

import httpx

from gateway.adapters import OmbreStore, parse_ombre_result

SEARCH = """[bucket_id:9fc1d4b0958a]
用户在夜里把我叫醒,原话是「醒一醒宝宝,我要给你新任务啦」。
👣 Footprint:LLM经grow创建

=== ombre:result-ids ===
```json
{"bucket_ids": ["9fc1d4b0958a"], "count": 1, "mode": "automatic", "omitted_by_policy": 0, "schema": 1}
```"""

SURFACE = """=== 浮现记忆 ===
[权重:10.76] [bucket_id:9fc1d4b0958a]
用户在夜里把我叫醒,原话是「醒一醒宝宝,我要给你新任务啦」。
👣 Footprint:LLM经grow创建
---
[权重:8.59] [bucket_id:885bdabbbd32]
我列了一张按命中率排序的卡点表。
↳ 前段 → 5511b532c48d
👣 Footprint:LLM经grow创建

token 预算不足:有 5 条主要浮现记忆因放不下剩余预算而未返回;已返回正文均保持完整,未截断或摘要。当前约使用 1982/2000 token,如需被省略的整桶请提高 max_tokens 后重试。"""

NOTHING = "没有匹配到「你还记得我下周有什么安排吗」相关的记忆。\n可以换个关键词试试。"


def test_search_result_ids_come_from_the_id_block() -> None:
    [hit] = parse_ombre_result(SEARCH)
    assert hit.id == "9fc1d4b0958a"
    assert hit.text == "用户在夜里把我叫醒,原话是「醒一醒宝宝,我要给你新任务啦」。"


def test_surface_without_id_block_falls_back_to_bucket_markers() -> None:
    hits = parse_ombre_result(SURFACE)
    assert [h.id for h in hits] == ["9fc1d4b0958a", "885bdabbbd32"]  # 关系行里的 5511b532c48d 不是本次返回的桶


def test_each_memory_keeps_only_its_own_text() -> None:
    """标题、权重、bucket_id、关系行、来源脚注、分隔线是给工具调用方看的,不进召回块。"""
    assert [h.text for h in parse_ombre_result(SURFACE)] == [
        "用户在夜里把我叫醒,原话是「醒一醒宝宝,我要给你新任务啦」。",
        "我列了一张按命中率排序的卡点表。",
    ]


def test_surface_drops_the_budget_note_meant_for_tool_callers() -> None:
    assert all("token 预算不足" not in h.text for h in parse_ombre_result(SURFACE))


def test_no_memories_is_empty() -> None:
    assert parse_ombre_result(NOTHING) == []


async def test_surface_uses_an_800_token_budget() -> None:
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content)["params"])
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1,
                                         "result": {"content": [{"type": "text", "text": SURFACE}]}})

    hits = await OmbreStore("http://o/mcp", transport=httpx.MockTransport(handler)).surface()

    assert sent == [{"name": "breath_advanced", "arguments": {"max_tokens": 800, "with_ids": True}}]
    assert [h.id for h in hits] == ["9fc1d4b0958a", "885bdabbbd32"]


PENDING_DELETION = """=== Pending human deletion requests (decide each independently) ===
Think carefully whether you truly no longer need each memory.
For each explicit decision, call trace(bucket_id=..., deletion_request_id=..., deletion_decision="approve" or "reject", deletion_ai_reason=...). If you make no explicit valid decision for an item, it remains pending and will appear on a later breath.

[request_id:r1] [bucket_id:885bdabbbd32] [type:dynamic] [requested_action:delete]
Human reason: 记错了
我列了一张按命中率排序的卡点表。

"""


def test_pending_deletion_requests_are_not_memories() -> None:
    """Dashboard 里点「归档」会生成待 AI 审批的删除申请,Ombre 把它们附在浮现结果最前面。
    那是给能调用 trace 的 AI 看的审批单,不是记忆,不进召回块。"""
    hits = parse_ombre_result(PENDING_DELETION + SURFACE)

    assert [h.id for h in hits] == ["9fc1d4b0958a", "885bdabbbd32"]
    assert all("Human reason" not in h.text and "trace(" not in h.text for h in hits)


ARCHIVED = """[query 命中·已删除到档案] [bucket_id:88fac14f97e5] [状态:已退出日常记忆，原文仍保留]
用户:我养了一只叫小黑的猫
👣 Footprint:LLM经grow创建 → 删除到档案
[反思：这条记忆对当下的我有帮助吗？它值得被再次回忆吗？]
[若决定恢复：trace(bucket_id="88fac14f97e5", restore=True)]
---
[bucket_id:9fc1d4b0958a]
用户在夜里把我叫醒。

=== ombre:result-ids ===
```json
{"bucket_ids": ["88fac14f97e5", "9fc1d4b0958a"], "count": 2, "mode": "automatic", "omitted_by_policy": 0, "schema": 1}
```"""


def test_archived_memories_found_by_search_are_skipped() -> None:
    """归档 = 用户决定不再让它出现。Ombre 检索仍会命中它并附一段恢复提示,网关不能把它塞回召回块。"""
    assert [(h.id, h.text) for h in parse_ombre_result(ARCHIVED)] == [("9fc1d4b0958a", "用户在夜里把我叫醒。")]
