"""DSH(DeepSeek Harness 桌面版)接入。

DSH 和 Kelivo 有两处不同,网关要认得:
- 宿主上下文:DSH 在用户那句话之后以用户消息身份注入运行环境(「Current runtime context. …」)、
  技能清单(「<system-reminder>…」),并每隔一段时间追加时间(「Time sampled while preparing turn N, step M: …」)。
  它们不是用户说的话:不能当成本轮的用户消息、不能拿去召回、不能写进记忆;原样留给上游模型看。
- 后台请求:起会话标题、压缩上下文(compaction)。这些不是对话,不召回、不记成一轮;
  压缩和 Kelivo 一样,先把这一段写进两个记忆库。
提示词取自 DSH 2.0.18 随包的 dsh-time-context、dsh-system-prompt、dsh-tool-skill、dsh-session-title-*-llm、
dsh-compaction-basic;消息顺序取自 2026-10-29 的真实抓包。
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


RUNTIME = _u("Current runtime context. This snapshot supersedes earlier runtime-context snapshots.\n\n"
             "Current working directory: C:\\Users\\x\\workspace")
SKILLS = _u("<system-reminder>\nA skill is a reusable set of task-specific instructions. The following skills are "
            "available in this session:\n\n<available_skills>\n</available_skills>\n</system-reminder>")


def test_first_dsh_request_with_runtime_and_skill_snapshots(harness: Harness) -> None:
    """真实抓包里第一轮的形状:[系统][用户那句话][运行环境][技能清单][时间]。"""
    sent = [SYSTEM, _u("我下周六要去南昌看演唱会"), RUNTIME, SKILLS, _time(1)]
    harness.chat(sent)

    [turn] = harness.turns()
    assert turn["user_text"] == "我下周六要去南昌看演唱会"
    assert harness.facts.queries == ["我下周六要去南昌看演唱会"]
    out = harness.upstream.last["messages"]
    assert out[-5]["role"] == "system" and out[-4:] == sent[1:]  # 召回块在用户那句话前,注入的上下文原样跟在后面


def test_second_dsh_turn_settles_the_first_with_the_users_words(harness: Harness) -> None:
    first = [SYSTEM, _u("我下周六要去南昌看演唱会"), RUNTIME, SKILLS, _time(1)]
    harness.chat(first)
    harness.chat([*first, _a("好呀"), _u("有点紧张")])
    harness.tick()

    assert [(t["user_text"], t["status"]) for t in harness.turns()] == [("我下周六要去南昌看演唱会", "settled"),
                                                                        ("有点紧张", "pending")]
    transcript = harness.facts.writes[-1]["transcript"]
    assert "南昌看演唱会" in transcript and "available_skills" not in transcript


def test_a_user_who_types_system_reminder_mid_sentence_is_still_heard(harness: Harness) -> None:
    harness.chat([SYSTEM, _u("你知道 <system-reminder> 是什么吗")])

    assert harness.turns()[0]["user_text"] == "你知道 <system-reminder> 是什么吗"


def test_mood_plugin_judge_requests_never_become_turns(harness: Harness) -> None:
    """dsh-feiyu-mood 每轮聊完让模型判断心情,只要 JSON。这不是对话,不召回、不进记忆。"""
    harness.chat([SYSTEM, _u("我叫小鱼"), _time(1)])
    judge = _u("**你这轮说的话（判据之一，重点看语气）：**\n好呀\n\n筱筱这轮说：\n在吗\n\n"
               "请把「你怎么说的」和「筱筱说了什么」合起来判断你的心情变化，只输出 JSON。")

    harness.chat([{"role": "system", "content": "你在判断自己的心情"}, judge])

    [turn] = harness.turns()
    assert turn["user_text"] == "我叫小鱼" and turn["status"] == "pending"
    assert harness.facts.queries == ["我叫小鱼"]


def test_user_mentioning_json_is_still_heard(harness: Harness) -> None:
    harness.chat([_u("帮我把这段话改成只输出 JSON 的格式")])
    assert len(harness.turns()) == 1
