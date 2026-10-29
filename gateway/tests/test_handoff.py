"""工单 08:段的第一轮带上另一台设备刚结束那段的最后 6 轮原文(ADR-0004)。"""

from typing import Any

from conftest import PHONE_TOKEN, Harness


def _conversation(n: int) -> list[dict[str, Any]]:
    msgs: list[dict[str, Any]] = []
    for i in range(1, n):
        msgs += [{"role": "user", "content": f"电脑上第{i}句"}, {"role": "assistant", "content": "好的"}]
    return msgs + [{"role": "user", "content": f"电脑上第{n}句"}]


def _talk_on_pc(harness: Harness, turns: int) -> None:
    for n in range(1, turns + 1):
        harness.chat(_conversation(n))


def _block(harness: Harness) -> str:
    content: str = harness.upstream.last["messages"][-2]["content"]
    return content


def test_phone_picks_up_the_last_six_turns_from_pc(harness: Harness) -> None:
    _talk_on_pc(harness, 8)
    harness.clock.advance(minutes=10)

    harness.chat([{"role": "user", "content": "我到家了"}], token=PHONE_TOKEN)

    block = _block(harness)
    assert "刚才在另一台设备上的对话" in block
    assert "电脑上第8句" in block and "电脑上第3句" in block
    assert "电脑上第2句" not in block


def test_no_handoff_when_other_device_ended_more_than_six_hours_ago(harness: Harness) -> None:
    _talk_on_pc(harness, 2)
    harness.clock.advance(hours=7)

    harness.chat([{"role": "user", "content": "早"}], token=PHONE_TOKEN)

    assert "另一台设备" not in _block(harness)


def test_no_handoff_on_the_same_device(harness: Harness) -> None:
    _talk_on_pc(harness, 2)
    harness.clock.advance(minutes=40)

    harness.chat([{"role": "user", "content": "我回来了"}])

    assert "另一台设备" not in _block(harness)


def test_no_handoff_when_the_latest_episode_was_on_this_device(harness: Harness) -> None:
    harness.chat([{"role": "user", "content": "手机上聊的旧事"}], token=PHONE_TOKEN)
    harness.clock.advance(minutes=31)
    _talk_on_pc(harness, 2)
    harness.clock.advance(minutes=31)

    harness.chat([{"role": "user", "content": "电脑上又开一段"}])

    assert "另一台设备" not in _block(harness)


def test_handoff_turns_are_audited(harness: Harness) -> None:
    _talk_on_pc(harness, 2)

    harness.chat([{"role": "user", "content": "我到家了"}], token=PHONE_TOKEN)

    handoff_ids = [i for i in harness.audit()[-1]["memory_ids"] if i.startswith("handoff:")]
    assert len(handoff_ids) == 2


def test_handoff_only_on_first_turn_of_the_phone_episode(harness: Harness) -> None:
    _talk_on_pc(harness, 2)
    harness.chat([{"role": "user", "content": "换手机了"}], token=PHONE_TOKEN)
    harness.chat([{"role": "user", "content": "换手机了"}, {"role": "assistant", "content": "好的"},
                  {"role": "user", "content": "继续"}], token=PHONE_TOKEN)

    assert "另一台设备" not in _block(harness)
