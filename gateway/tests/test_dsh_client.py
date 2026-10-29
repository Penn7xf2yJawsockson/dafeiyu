"""DSH(DeepSeek Harness 桌面版)接入。

DSH 和 Kelivo 有两处不同,网关要认得:
- 时间上下文:DSH 每隔一段时间在请求末尾追加一条用户消息「Time sampled while preparing turn N, step M: …」。
  它不是用户说的话:不能当成本轮的用户消息、不能拿去召回、不能写进记忆;原样留给上游模型看。
- 后台请求:起会话标题、压缩上下文(compaction)。这些不是对话,不召回、不记成一轮;
  压缩和 Kelivo 一样,先把这一段写进两个记忆库。
提示词取自 DSH 2.0.18 随包的 @deepseek-ai/dsh-time-context、dsh-session-title-*-llm、dsh-compaction-basic。
"""

from typing import Any

import pytest
from conftest import Harness

from gateway.stores import MemoryHit


def _time(turn: int, step: int = 1, elapsed: str = "unavailable") -> dict[str, Any]:
    text = (f"Time sampled while preparing turn {turn}, step {step}: 2026-10-29T22:05:33+08:00[Etc/GMT-8]\n"
            "Browser time zone for this request: Etc/GMT-8. Interpret otherwise-unqualified dates and times in "
            f"this zone.\nElapsed since the preceding model-visible message: {elapsed}.")
    return {"role": "user", "content": [{"type": "text", "text": text}]}


def _u(text: str) -> dict[str, Any]:
    return {"role": "user", "content": [{"type": "text", "text": text}]}


def _a(text: str) -> dict[str, Any]:
    return {"role": "assistant", "content": text}


SYSTEM = {"role": "system", "content": "You are DSH, a coding agent."}
TITLE_SYSTEM = ("Create a concise title for an AI coding-assistant session from the supplied human messages.\n"
                "Return only the title on one line")
COMPACTION = ("You are now acting as a compaction engine for this AI coding assistant. Condense the conversation "
              "ABOVE into a structured checkpoint that lets another model resume the work with no loss of essential "
              "context.\n\n## Primary Request and Intent")


def test_time_context_is_not_the_users_words(harness: Harness) -> None:
    harness.chat([SYSTEM, _u("醒一醒宝宝,我要给你新任务啦"), _time(1)])

    [turn] = harness.turns()
    assert turn["user_text"] == "醒一醒宝宝,我要给你新任务啦"
    assert harness.facts.queries == ["醒一醒宝宝,我要给你新任务啦"]


def test_time_context_still_reaches_the_model_right_after_the_users_words(harness: Harness) -> None:
    harness.facts.hits = [MemoryHit("f1", "用户叫小鱼")]
    harness.chat([SYSTEM, _u("在吗"), _time(1)])

    sent = harness.upstream.last["messages"]
    assert "用户叫小鱼" in sent[-3]["content"]  # 召回块在用户那句话前面
    assert sent[-2] == _u("在吗")
    assert sent[-1] == _time(1)


def test_next_turn_settles_the_previous_one_even_though_time_lines_differ(harness: Harness) -> None:
    harness.chat([SYSTEM, _u("我叫小鱼"), _time(1)])
    harness.chat([SYSTEM, _u("我叫小鱼"), _time(1), _a("记住啦"), _u("我住杭州"), _time(2, elapsed="41m 14s")])

    assert [(t["user_text"], t["status"]) for t in harness.turns()] == [("我叫小鱼", "settled"),
                                                                        ("我住杭州", "pending")]


def test_regenerating_with_a_fresh_time_line_supersedes(harness: Harness) -> None:
    harness.chat([SYSTEM, _u("我叫小鱼"), _a("记住啦"), _u("讲个笑话"), _time(2)])
    harness.chat([SYSTEM, _u("我叫小鱼"), _a("记住啦"), _u("讲个笑话"), _time(2, elapsed="5s")])

    statuses = [(t["user_text"], t["status"]) for t in harness.turns()]
    assert statuses[-2:] == [("讲个笑话", "superseded"), ("讲个笑话", "pending")]


def test_time_line_after_a_tool_result_keeps_the_tool_round(harness: Harness) -> None:
    call = {"id": "c1", "type": "function", "function": {"name": "web_search", "arguments": "{}"}}
    harness.upstream.script = [{"tool_calls": [call]}, {"content": "明天小雨"}]
    ask = [SYSTEM, _u("明天杭州天气"), _time(1)]
    harness.chat(ask)
    harness.chat([*ask, {"role": "assistant", "content": None, "tool_calls": [call]},
                  {"role": "tool", "tool_call_id": "c1", "content": "小雨"}, _time(1, step=2)])

    [turn] = harness.turns()
    assert turn["user_text"] == "明天杭州天气" and turn["assistant_text"] == "明天小雨"
    assert harness.facts.queries == ["明天杭州天气"]


def test_request_with_only_a_time_line_is_rejected_like_any_request_without_user_words(harness: Harness) -> None:
    assert harness.chat([SYSTEM, _time(1)]).status_code == 400


@pytest.mark.parametrize("framing", [
    'Generate the session title from this JSON array of human messages:\n[{"seq":18,"text":"醒一醒"}]',
    'Update the session title from this JSON object:\n{"currentTitle":"x","messages":[]}',
])
def test_session_title_requests_never_become_turns(harness: Harness, framing: str) -> None:
    harness.chat([SYSTEM, _u("我叫小鱼"), _time(1)])
    sent = [{"role": "system", "content": TITLE_SYSTEM}, _u(framing)]

    harness.chat(sent)

    assert harness.upstream.last["messages"] == sent
    [turn] = harness.turns()
    assert turn["user_text"] == "我叫小鱼" and turn["status"] == "pending"


def test_compaction_is_passed_through_and_writes_the_episode(harness: Harness) -> None:
    history = [SYSTEM, _u("我下周三生日"), _time(1), _a("记住啦")]
    harness.chat(history[:3])

    harness.chat([*history, _u(COMPACTION)])
    harness.tick()

    assert harness.upstream.last["messages"] == [*history, _u(COMPACTION)]
    [turn] = harness.turns()
    assert turn["status"] == "settled"
    assert "我下周三生日" in harness.affect.writes[-1]["transcript"]
