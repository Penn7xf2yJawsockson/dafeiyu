"""工单 04:召回块固定插在深度 1,缓存前缀每轮不变(ADR-0003)。"""

from typing import Any

from conftest import Harness

from gateway.stores import MemoryHit


def _recall_block(sent: dict[str, Any]) -> str:
    msgs = sent["messages"]
    assert msgs[-1]["role"] == "user"
    block = msgs[-2]
    assert block["role"] == "system"
    content: str = block["content"]
    assert content.startswith("<memory>") and content.endswith("</memory>")
    return content


def test_recall_block_sits_right_before_latest_user_message(harness: Harness) -> None:
    harness.facts.hits = [MemoryHit("f2", "用户养了一只叫团子的猫"), MemoryHit("f1", "用户在杭州工作")]

    harness.chat([{"role": "user", "content": "团子今天又捣乱了"}])

    block = _recall_block(harness.upstream.last)
    assert "用户养了一只叫团子的猫" in block
    assert block.index("用户在杭州工作") < block.index("团子的猫")  # 同类按记忆 ID 排序
    assert harness.facts.queries == ["团子今天又捣乱了"]


def test_empty_recall_still_keeps_a_fixed_placeholder(harness: Harness) -> None:
    harness.chat([{"role": "user", "content": "嗨"}])
    first = _recall_block(harness.upstream.last)
    harness.chat([{"role": "user", "content": "又是我"}])

    assert _recall_block(harness.upstream.last) == first


def test_prefix_before_recall_block_is_identical_across_turns(harness: Harness) -> None:
    harness.facts.hits = [MemoryHit("f1", "第一轮的记忆")]
    harness.upstream.reply = "嗯嗯"
    harness.chat([{"role": "system", "content": "x"}, {"role": "user", "content": "第一句"}])
    turn1 = harness.upstream.last["messages"]

    harness.facts.hits = [MemoryHit("f9", "第二轮完全不同的记忆")]
    harness.chat([
        {"role": "system", "content": "x"},
        {"role": "user", "content": "第一句"},
        {"role": "assistant", "content": "嗯嗯"},
        {"role": "user", "content": "第二句"},
    ])
    turn2 = harness.upstream.last["messages"]

    prefix1 = turn1[:-2]
    assert turn2[: len(prefix1)] == prefix1
    assert all("第一轮的记忆" not in str(m["content"]) for m in turn2)


def test_recalled_memory_ids_are_audited(harness: Harness) -> None:
    harness.facts.hits = [MemoryHit("f1", "a"), MemoryHit("f2", "b")]
    harness.affect.hits = [MemoryHit("o7", "c")]

    harness.chat([{"role": "user", "content": "最近怎么样"}])

    [entry] = harness.audit()
    assert set(entry["memory_ids"]) == {"facts:f1", "facts:f2", "affect:o7"}
    assert entry["outcome"] == "ok"
