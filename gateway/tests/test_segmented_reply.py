"""DSH 分段回复插件(dsh-segmented-reply):模型用 send_message 工具一条一条发消息,最终回复为空。

这一轮大肥鱼说的话在 send_message 的参数里;网关要把它们按顺序拼起来记成这一轮的回复,
否则记忆里只有用户说的话。工具调用本身仍不进记忆(工单 tool-calls 的约定不变)。
"""

import json
from typing import Any

from conftest import Harness


def _call(i: int, content: str) -> dict[str, Any]:
    return {"id": f"c{i}", "type": "function",
            "function": {"name": "send_message", "arguments": json.dumps({"content": content}, ensure_ascii=False)}}


def _result(i: int, content: str) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": f"c{i}", "content": content}


ASK = {"role": "user", "content": "晚上好呀"}


def _segmented_turn(harness: Harness, final: str = "") -> list[dict[str, Any]]:
    calls = [_call(1, "晚上好~"), _call(2, "今天过得怎么样")]
    harness.upstream.script = [{"tool_calls": calls}, {"content": final}]
    harness.chat([ASK])
    follow: list[dict[str, Any]] = [ASK, {"role": "assistant", "content": None, "tool_calls": calls},
              _result(1, "晚上好~"), _result(2, "今天过得怎么样")]
    harness.chat(follow)
    return follow


def test_sent_messages_become_the_reply_in_memory(harness: Harness) -> None:
    _segmented_turn(harness)

    [turn] = harness.turns()
    assert turn["user_text"] == "晚上好呀"
    assert turn["assistant_text"] == "晚上好~\n今天过得怎么样"


def test_a_final_text_after_the_messages_is_kept_too(harness: Harness) -> None:
    _segmented_turn(harness, final="想我了没")
    assert harness.turns()[0]["assistant_text"] == "晚上好~\n今天过得怎么样\n想我了没"


def test_messages_reach_both_memory_stores(harness: Harness) -> None:
    history = _segmented_turn(harness)
    harness.chat([*history, {"role": "assistant", "content": ""}, {"role": "user", "content": "还行"}])
    harness.clock.advance(minutes=31)
    harness.tick()
    for store in (harness.facts, harness.affect):
        transcript = store.writes[-1]["transcript"]
        assert "今天过得怎么样" in transcript and "send_message" not in transcript


def test_other_tools_still_do_not_reach_memory(harness: Harness) -> None:
    call = {"id": "c1", "type": "function", "function": {"name": "web_search", "arguments": '{"q": "天气"}'}}
    harness.upstream.script = [{"tool_calls": [call]}, {"content": "明天晴"}]
    harness.chat([ASK])
    harness.chat([ASK, {"role": "assistant", "content": None, "tool_calls": [call]}, _result(1, "晴")])
    assert harness.turns()[0]["assistant_text"] == "明天晴"
