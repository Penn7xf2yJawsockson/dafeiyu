"""带工具的一轮:用户说一句 → 模型要调用工具 → Kelivo 执行后回传结果 → 模型给出最终回复。

整个过程算同一轮;只有用户说的话和最终回复写进记忆,工具调用与工具结果不进(用户 2026-10-29 确认)。
"""

import json
from typing import Any

import httpx
from conftest import Harness

from gateway.stores import MemoryHit

TOOLS: list[dict[str, Any]] = [{"type": "function", "function": {"name": "web_search", "description": "联网搜索",
                                           "parameters": {"type": "object", "properties": {"q": {"type": "string"}}}}}]
CALL: dict[str, Any] = {"id": "call_1", "type": "function", "function": {"name": "web_search", "arguments": '{"q": "杭州明天天气"}'}}
ASK: dict[str, Any] = {"role": "user", "content": "明天杭州天气怎么样"}
RESULT = "搜索结果:杭州 明天 小雨 15-19℃ 东北风3级 ……(以下省略三千字网页内容)"
FINAL = "明天杭州有小雨,记得带伞"
NOTICE = "⚠ 记忆不完整"


def _tool_turn(harness: Harness, stream: bool = False) -> tuple[httpx.Response, httpx.Response, list[dict[str, Any]]]:
    harness.upstream.script = [{"tool_calls": [CALL]}, {"content": FINAL}]
    first = harness.chat([ASK], stream=stream, tools=TOOLS)
    follow: list[dict[str, Any]] = [ASK, {"role": "assistant", "content": None, "tool_calls": [CALL]},
              {"role": "tool", "tool_call_id": "call_1", "content": RESULT}]
    final = harness.chat(follow, stream=stream, tools=TOOLS)
    return first, final, follow + [{"role": "assistant", "content": FINAL}]


def test_whole_tool_loop_is_one_turn_that_ends_with_the_final_reply(harness: Harness) -> None:
    _tool_turn(harness)

    [turn] = harness.turns()
    assert turn["user_text"] == "明天杭州天气怎么样"
    assert turn["assistant_text"] == FINAL
    assert harness.facts.queries == ["明天杭州天气怎么样"]  # 工具回合不再重新召回


def test_tool_result_stays_right_after_its_call_and_the_prefix_does_not_move(harness: Harness) -> None:
    harness.facts.hits = [MemoryHit("f1", "用户住在杭州")]
    _tool_turn(harness)

    first, follow = (r["messages"] for r in harness.upstream.requests)
    assert [m["role"] for m in follow[-3:]] == ["user", "assistant", "tool"]
    assert follow[: len(first)] == first  # 召回块原样留在用户那句话前面,缓存前缀不变
    assert "用户住在杭州" in follow[-4]["content"]
    assert follow[-1]["content"] == RESULT


def test_only_user_words_and_final_reply_reach_memory(harness: Harness) -> None:
    _, _, history = _tool_turn(harness)
    harness.chat(history + [{"role": "user", "content": "好的谢谢"}])
    harness.clock.advance(minutes=31)
    harness.tick()

    for writes in (harness.facts.writes, harness.affect.writes):
        transcript = writes[-1]["transcript"]
        assert "明天杭州天气怎么样" in transcript and FINAL in transcript
        assert "搜索结果" not in transcript and "web_search" not in transcript


def test_degraded_notice_goes_only_on_the_final_reply(harness: Harness) -> None:
    harness.affect.fail = True

    first, final, _ = _tool_turn(harness, stream=True)

    assert NOTICE not in first.text
    assert NOTICE in "".join(json.loads(line[6:])["choices"][0]["delta"].get("content") or ""
                             for line in final.text.splitlines()
                             if line.startswith("data: ") and line != "data: [DONE]")


def test_tool_call_stream_is_relayed_intact(harness: Harness) -> None:
    first, _, _ = _tool_turn(harness, stream=True)

    deltas = [json.loads(line[6:])["choices"][0] for line in first.text.splitlines()
              if line.startswith("data: ") and line != "data: [DONE]"]
    calls = [c["delta"]["tool_calls"][0] for c in deltas if c["delta"].get("tool_calls")]
    assert calls[0]["function"]["name"] == "web_search"
    assert "".join(c["function"].get("arguments", "") for c in calls) == CALL["function"]["arguments"]
    assert deltas[-1]["finish_reason"] == "tool_calls"


def test_tools_used_are_audited(harness: Harness) -> None:
    _tool_turn(harness)

    assert ["web_search"] in [entry["tool_calls"] for entry in harness.audit()]


def test_regenerating_a_tool_turn_supersedes_it(harness: Harness) -> None:
    _tool_turn(harness)
    harness.upstream.script = [{"content": "直接回答:明天小雨"}]
    harness.chat([ASK], tools=TOOLS)  # 在 Kelivo 里对这一轮点了重新生成

    assert [t["status"] for t in harness.turns()] == ["superseded", "pending"]


def test_tool_continuation_without_a_known_turn_still_gets_a_valid_request(harness: Harness) -> None:
    """例如网关在工具调用中途重启,或原文日志里找不到这一轮:照样能回答,并补记为一轮。"""
    harness.upstream.script = [{"content": FINAL}]
    follow: list[dict[str, Any]] = [ASK, {"role": "assistant", "content": None, "tool_calls": [CALL]},
              {"role": "tool", "tool_call_id": "call_1", "content": RESULT}]

    resp = harness.chat(follow, tools=TOOLS)

    assert resp.status_code == 200
    assert [m["role"] for m in harness.upstream.last["messages"][-4:]] == ["system", "user", "assistant", "tool"]
    [turn] = harness.turns()
    assert turn["user_text"] == "明天杭州天气怎么样" and turn["assistant_text"] == FINAL
