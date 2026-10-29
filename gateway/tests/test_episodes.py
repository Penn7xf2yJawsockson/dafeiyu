"""工单 05/06:段的切分;情感库按段写入;情感浮现只在段的第一轮。"""

from typing import Any

from conftest import Harness

from gateway.stores import MemoryHit


def _conversation(n: int) -> list[dict[str, Any]]:
    """第 n 轮(从 1 开始)时 Kelivo 发来的历史:前 n-1 轮问答 + 本轮问题。"""
    msgs: list[dict[str, Any]] = []
    for i in range(1, n):
        msgs += [{"role": "user", "content": f"问{i}"}, {"role": "assistant", "content": "好的"}]
    return msgs + [{"role": "user", "content": f"问{n}"}]


def test_episode_closes_after_idle_and_is_written_to_affect_store_once(harness: Harness) -> None:
    harness.chat(_conversation(1))
    harness.chat(_conversation(2))
    harness.clock.advance(minutes=31)
    harness.tick()
    harness.tick()

    [write] = harness.affect.writes
    assert "问1" in write["transcript"] and "问2" in write["transcript"]
    assert [t["status"] for t in harness.turns()] == ["settled", "settled"]


def test_message_after_idle_starts_a_new_episode(harness: Harness) -> None:
    harness.chat(_conversation(1))
    harness.clock.advance(minutes=31)
    harness.chat(_conversation(2))
    harness.tick()

    first, second = harness.turns()
    assert first["episode_id"] != second["episode_id"]
    assert len(harness.affect.writes) == 1


def test_episode_closes_after_twenty_turns(harness: Harness) -> None:
    for n in range(1, 22):
        harness.chat(_conversation(n))
    harness.tick()

    turns = harness.turns()
    assert len({t["episode_id"] for t in turns[:20]}) == 1
    assert turns[20]["episode_id"] != turns[0]["episode_id"]
    [write] = harness.affect.writes
    assert "问20" in write["transcript"] and "问21" not in write["transcript"]


def test_superseded_turns_never_reach_affect_store(harness: Harness) -> None:
    harness.upstream.reply = "被放弃的回复"
    harness.chat(_conversation(1))
    harness.upstream.reply = "好的"
    harness.chat(_conversation(1))
    harness.clock.advance(minutes=31)
    harness.tick()

    [write] = harness.affect.writes
    assert "被放弃的回复" not in write["transcript"]


def test_regenerating_the_first_turn_still_surfaces(harness: Harness) -> None:
    harness.affect.surfaced = [MemoryHit("o1", "上周面试的结果还没出来")]
    harness.chat(_conversation(1))
    harness.chat(_conversation(1))  # 重新生成段的第一轮

    assert "面试的结果还没出来" in harness.upstream.last["messages"][-2]["content"]
    first, second = harness.turns()
    assert first["episode_id"] == second["episode_id"]


def test_affect_surfacing_happens_only_on_first_turn_of_episode(harness: Harness) -> None:
    harness.affect.surfaced = [MemoryHit("o1", "上周面试的结果还没出来,用户很焦虑")]

    harness.chat(_conversation(1))
    first = harness.upstream.last["messages"][-2]["content"]
    harness.chat(_conversation(2))
    second = harness.upstream.last["messages"][-2]["content"]

    assert "面试的结果还没出来" in first
    assert "面试的结果还没出来" not in second


def test_a_memory_already_surfaced_is_not_listed_again_under_affect(harness: Harness) -> None:
    """同一个桶既被段首浮现、又被这一轮检索命中时,只在「心里还挂着的事」里出现一次。"""
    harness.affect.surfaced = [MemoryHit("o1", "上周面试的结果还没出来")]
    harness.affect.hits = [MemoryHit("o1", "上周面试的结果还没出来"), MemoryHit("o2", "用户喜欢猫")]

    harness.chat(_conversation(1))
    block = harness.upstream.last["messages"][-2]["content"]

    assert block.count("上周面试的结果还没出来") == 1
    assert "用户喜欢猫" in block
    assert harness.audit()[-1]["memory_ids"].count("affect:o1") == 0
