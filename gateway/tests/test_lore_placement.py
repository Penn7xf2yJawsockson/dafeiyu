"""世界书条目的注入位置,对照 SillyTavern 1.17(world-info.js 的 position 分配、script.js 的 doChatInject)。

发给上游的消息(深度从本轮用户这句话往前数):

    system  人设 / ↑角色定义 / 核心档案 / ↓角色定义
    system  客户端系统消息
    ……      历史,@D 条目插在其中
    system  召回块                   ← 深度 1
    user    本轮用户这句话            ← 深度 0
    @D0 条目
    ……      工具回合
"""

import json
from typing import Any

from conftest import Harness

from gateway.compose import compose
from gateway.lorebook import (AFTER, AN_BOTTOM, AN_TOP, AT_DEPTH, BEFORE, EM_TOP, OUTLET, ROLE_ASSISTANT, ROLE_SYSTEM,
                              ROLE_USER, LoreSettings, activate, entries_of, normalize_world, place)


def _u(text: str) -> dict[str, Any]:
    return {"role": "user", "content": text}


def _a(text: str) -> dict[str, Any]:
    return {"role": "assistant", "content": text}


def _placement(*entries: dict[str, Any]) -> Any:
    w = {"w": normalize_world({"entries": {str(i): {"uid": i, "key": ["猫"], **e} for i, e in enumerate(entries)}})}
    return place(activate(entries_of(w), ["猫"], LoreSettings()), LoreSettings())


def _flat(messages: list[dict[str, Any]]) -> list[str]:
    return [f"{m['role']}:{m['content']}" for m in messages]


# --- place():position → 落到哪 ---

def test_positions_map_like_sillytavern() -> None:
    p = _placement({"content": "上", "position": BEFORE}, {"content": "下", "position": AFTER},
                   {"content": "例", "position": EM_TOP}, {"content": "注上", "position": AN_TOP, "order": 1},
                   {"content": "注下", "position": AN_BOTTOM, "order": 2},
                   {"content": "深", "position": AT_DEPTH, "depth": 2, "role": ROLE_USER})
    assert p.before == ["上"] and p.after == ["下", "例"]
    assert p.depth == {(4, ROLE_SYSTEM): "注上\n注下", (2, ROLE_USER): "深"}


def test_same_depth_and_role_merge_in_order() -> None:
    p = _placement({"content": "B", "position": AT_DEPTH, "depth": 3, "order": 50},
                   {"content": "A", "position": AT_DEPTH, "depth": 3, "order": 10},
                   {"content": "C", "position": AT_DEPTH, "depth": 3, "role": ROLE_ASSISTANT})
    assert p.depth == {(3, ROLE_SYSTEM): "A\nB", (3, ROLE_ASSISTANT): "C"}


def test_at_depth_without_depth_uses_4() -> None:
    assert _placement({"content": "x", "position": AT_DEPTH, "depth": None}).depth == {(4, ROLE_SYSTEM): "x"}


def test_outlet_is_only_used_where_referenced() -> None:
    p = _placement({"content": "她怕打雷", "position": OUTLET, "outletName": "小秘密"})
    assert p.before == [] and p.depth == {}
    assert p.fill_outlets("记住:{{outlet::小秘密}}。{{OUTLET:: 没有 }}") == "记住:她怕打雷。"


# --- compose():@D 插进历史 ---

HISTORY = [_u("一"), _a("二"), _u("三"), _a("四"), _u("本轮")]


def _composed(depth: dict[tuple[int, int], str], history: list[dict[str, Any]] = HISTORY,
              start: int = 4) -> list[str]:
    return _flat(compose(["人设"], history, "召回", start, depth=depth))


def test_depth_counts_back_from_this_turns_user_message() -> None:
    assert _composed({(0, ROLE_SYSTEM): "D0"}) == [
        "system:人设", "user:一", "assistant:二", "user:三", "assistant:四", "system:召回", "user:本轮", "system:D0"]
    assert _composed({(1, ROLE_SYSTEM): "D1"})[-4:] == ["assistant:四", "system:D1", "system:召回", "user:本轮"]
    assert _composed({(2, ROLE_SYSTEM): "D2"})[3:6] == ["user:三", "system:D2", "assistant:四"]
    assert _composed({(4, ROLE_SYSTEM): "D4"})[1:4] == ["user:一", "system:D4", "assistant:二"]


def test_depth_beyond_history_goes_to_the_top_of_history() -> None:
    assert _composed({(99, ROLE_SYSTEM): "深"})[:3] == ["system:人设", "system:深", "user:一"]


def test_same_spot_orders_deeper_first_then_assistant_user_system() -> None:
    got = _composed({(1, ROLE_SYSTEM): "S", (1, ROLE_USER): "U", (1, ROLE_ASSISTANT): "A"})
    assert got[-6:] == ["assistant:四", "assistant:A", "user:U", "system:S", "system:召回", "user:本轮"]


def test_depth_zero_goes_before_tool_rounds_and_client_context() -> None:
    call = {"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}]}
    history = [_u("问"), call, {"role": "tool", "tool_call_id": "c1", "content": "结果"}]
    got = compose(["人设"], history, "召回", 0, depth={(0, ROLE_SYSTEM): "D0"})
    assert [m["role"] for m in got] == ["system", "system", "user", "system", "assistant", "tool"]


def test_never_splits_a_tool_call_from_its_result() -> None:
    call = {"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}]}
    history = [_u("早先"), call, {"role": "tool", "tool_call_id": "c1", "content": "r"}, _a("答"), _u("本轮")]
    # 深度 3 正好落在工具结果上:往前退到工具调用之前,不拆开它们
    got = compose(["人设"], history, "召回", 4, depth={(3, ROLE_SYSTEM): "D3"})
    assert [m["role"] for m in got][:5] == ["system", "user", "system", "assistant", "tool"]
    assert got[2] == {"role": "system", "content": "D3"}
    # 深度 2 落在「答」之前,本来就在工具结果之后
    got = compose(["人设"], history, "召回", 4, depth={(2, ROLE_SYSTEM): "D2"})
    assert [m["role"] for m in got][3:6] == ["tool", "system", "assistant"]


# --- 整条链路 ---

def _write_world(harness: Harness, *entries: dict[str, Any]) -> None:
    (harness.data_dir / "worlds").mkdir(exist_ok=True)
    (harness.data_dir / "worlds" / "w.json").write_text(
        json.dumps({"entries": {str(i): {"uid": i, **e} for i, e in enumerate(entries)}}, ensure_ascii=False),
        encoding="utf-8")


def test_full_layout_with_every_kind_of_position(harness: Harness) -> None:
    (harness.data_dir / "core_profile.md").write_text("档案。{{outlet::秘密}}", encoding="utf-8")
    _write_world(harness,
                 {"key": ["猫"], "content": "上", "position": BEFORE},
                 {"key": ["猫"], "content": "下", "position": AFTER},
                 {"key": ["猫"], "content": "怕打雷", "position": OUTLET, "outletName": "秘密"},
                 {"key": ["猫"], "content": "D1用户", "position": AT_DEPTH, "depth": 1, "role": ROLE_USER},
                 {"key": ["猫"], "content": "D0", "position": AT_DEPTH, "depth": 0})

    harness.chat([{"role": "system", "content": "客户端"}, _u("在吗"), _a("在"), _u("猫呢")])

    assert _flat(harness.upstream.last["messages"]) == [
        "system:你是大肥鱼。\n\n上\n\n档案。怕打雷\n\n下", "system:客户端",
        "user:在吗", "assistant:在", "user:D1用户", f"system:{harness.upstream.last['messages'][5]['content']}",
        "user:猫呢", "system:D0"]
    assert harness.upstream.last["messages"][5]["content"].startswith("<memory>")
    assert sorted(i for i in harness.audit()[-1]["memory_ids"] if i.startswith("lore:")) == [
        "lore:w/0", "lore:w/1", "lore:w/2", "lore:w/3", "lore:w/4"]


def test_probability_and_groups_are_stable_within_one_turns_tool_rounds(harness: Harness) -> None:
    """同一轮的工具回合要重算世界书:用这一轮的 ID 做随机种子,结果相同,缓存前缀不变。"""
    _write_world(harness, *[{"key": ["天气"], "content": f"条目{i}", "probability": 50, "position": AT_DEPTH, "depth": 1}
                            for i in range(8)])
    call = {"id": "c1", "type": "function", "function": {"name": "web_search", "arguments": "{}"}}
    harness.upstream.script = [{"tool_calls": [call]}, {"content": "晴"}]
    ask = [_u("明天天气")]
    harness.chat(ask)
    first = harness.upstream.last["messages"]
    harness.chat([*ask, {"role": "assistant", "content": None, "tool_calls": [call]},
                  {"role": "tool", "tool_call_id": "c1", "content": "晴"}])
    assert harness.upstream.last["messages"][: len(first)] == first
