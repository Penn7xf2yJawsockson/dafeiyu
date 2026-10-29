"""Kelivo 压缩上下文时,网关先把这一段记忆写完(用户 2026-10-29 确认)。

压缩只改变模型能看到的历史,不碰原文日志;但情感库原本要等段结束才写。压缩请求一到:
确定这台设备的待定轮、结束当前段并写入两个记忆库;压缩后的第一轮按新一段处理(重新浮现)。
"""

from typing import Any

import pytest
from conftest import PHONE_TOKEN, Harness

from gateway.stores import MemoryHit

COMPRESS = ("Summarize the following conversation history so the conversation can continue using the summary.\n\n"
            "<conversation>\n用户:我下周三生日\n大肥鱼:记住啦\n</conversation>")


def _u(text: str) -> dict[str, Any]:
    return {"role": "user", "content": text}


def _a(text: str) -> dict[str, Any]:
    return {"role": "assistant", "content": text}


def _two_turns(harness: Harness) -> None:
    harness.chat([_u("我下周三生日")])
    harness.chat([_u("我下周三生日"), _a("好的"), _u("记得提醒我")])


@pytest.mark.parametrize("model", ["dafeiyu", "dafeiyu-direct"])
def test_compress_request_writes_the_whole_episode_to_both_stores(harness: Harness, model: str) -> None:
    _two_turns(harness)

    harness.chat([_u(COMPRESS)], model=model)
    harness.tick()

    assert [t["status"] for t in harness.turns()] == ["settled", "settled"]
    for store in (harness.facts, harness.affect):
        transcript = store.writes[-1]["transcript"]
        assert "我下周三生日" in transcript and "记得提醒我" in transcript
        assert "Summarize the following" not in transcript  # 摘要请求本身不进记忆


def test_first_turn_after_compress_starts_a_new_episode_and_surfaces(harness: Harness) -> None:
    harness.affect.surfaced = [MemoryHit("o1", "用户下周三生日,有点期待")]
    _two_turns(harness)
    harness.chat([_u(COMPRESS)])

    summary = "(压缩后的摘要)用户说下周三生日,请大肥鱼提醒。"
    harness.chat([_u(summary), _u("那天我们做什么好")])

    first, second, third = harness.turns()
    assert third["episode_id"] != first["episode_id"]
    assert "用户下周三生日,有点期待" in harness.upstream.last["messages"][-2]["content"]


def test_compress_only_closes_the_episode_of_the_device_that_compressed(harness: Harness) -> None:
    harness.chat([_u("手机上说的话")], token=PHONE_TOKEN)
    _two_turns(harness)

    harness.chat([_u(COMPRESS)])

    statuses = {t["user_text"]: t["status"] for t in harness.turns()}
    assert statuses["手机上说的话"] == "pending"


def test_compress_with_nothing_to_sync_is_harmless(harness: Harness) -> None:
    resp = harness.chat([_u(COMPRESS)])
    harness.tick()

    assert resp.status_code == 200
    assert harness.turns() == [] and harness.affect.writes == []
