"""网关世界书的匹配规则,对照 SillyTavern 1.17 world-info.js 的行为。"""

from typing import Any

import pytest

from gateway.lorebook import (AND_ALL, AND_ANY, NOT_ALL, NOT_ANY, LoreSettings, activate, entries_of,
                              normalize_world, render)

S = LoreSettings(user_name="小鱼", char_name="大肥鱼")


def _world(*entries: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """酒馆导出的形状:entries 是以 uid 为键的对象;酒馆旧导出里常见 selectiveLogic 为 null。"""
    return {"w": normalize_world({"entries": {str(i): {"uid": i, "selectiveLogic": None, **e}
                                              for i, e in enumerate(entries)}})}


def _ids(worlds: dict[str, dict[str, Any]], *recent: str, s: LoreSettings = S, roll: float = 0.5) -> list[str]:
    return [e.uid for e in activate(entries_of(worlds), list(recent), s, rand=lambda: roll)]


def test_primary_key_is_plain_case_insensitive_substring_by_default() -> None:
    w = _world({"key": ["Shadowfang"], "content": "暗影之牙"})
    assert _ids(w, "我看到了SHADOWFANGS") == ["0"]
    assert _ids(w, "什么也没有") == []


def test_chinese_keys_match_inside_a_sentence_even_with_whole_words_on() -> None:
    """SillyTavern 的整词用 JS 的 \\W 当边界,中文字符算边界:中文关键词照样能在句子里命中。"""
    w = _world({"key": ["南昌"], "content": "南昌", "matchWholeWords": True})
    assert _ids(w, "下周去南昌看演唱会") == ["0"]


def test_whole_words_for_latin_keys() -> None:
    w = _world({"key": ["cat"], "content": "猫", "matchWholeWords": True})
    assert _ids(w, "my cat is cute") == ["0"]
    assert _ids(w, "concatenate") == []


def test_case_sensitive_entry_overrides_global() -> None:
    w = _world({"key": ["Bob"], "content": "x", "caseSensitive": True})
    assert _ids(w, "bob") == []
    assert _ids(w, "Bob") == ["0"]


def test_regex_keys_with_flags() -> None:
    w = _world({"key": ["/演唱会|音乐节/"], "content": "x"}, {"key": ["/^hello/i"], "content": "y"})
    assert _ids(w, "周末有个音乐节") == ["0"]
    assert _ids(w, "HELLO there") == ["1"]


def test_invalid_regex_falls_back_to_plain_text() -> None:
    w = _world({"key": ["/[unclosed/"], "content": "x"})
    assert _ids(w, "看看 /[unclosed/ 这个") == ["0"]


@pytest.mark.parametrize(("logic", "text", "fires"), [
    (AND_ANY, "猫和狗", True), (AND_ANY, "只有猫", False),
    (AND_ALL, "猫和狗和鸟", True), (AND_ALL, "猫和狗", False),
    (NOT_ANY, "只有猫", True), (NOT_ANY, "猫和鸟", False),
    (NOT_ALL, "猫和狗", True), (NOT_ALL, "猫和狗和鸟", False),
])
def test_secondary_key_logic(logic: int, text: str, fires: bool) -> None:
    w = _world({"key": ["猫"], "keysecondary": ["狗", "鸟"], "selectiveLogic": logic, "content": "x"})
    assert _ids(w, text) == (["0"] if fires else [])


def test_null_selective_logic_means_and_any() -> None:
    w = _world({"key": ["猫"], "keysecondary": ["狗"], "content": "x"})
    assert _ids(w, "猫") == []
    assert _ids(w, "猫狗") == ["0"]


def test_constant_always_fires_and_disabled_never_does() -> None:
    w = _world({"key": [], "constant": True, "content": "常驻"}, {"key": ["猫"], "disable": True, "content": "x"})
    assert _ids(w, "猫") == ["0"]


def test_entries_with_empty_content_are_skipped() -> None:
    assert _ids(_world({"key": ["猫"], "content": "  "}), "猫") == []


def test_scan_depth_global_and_per_entry() -> None:
    w = _world({"key": ["杭州"], "content": "a"}, {"key": ["杭州"], "content": "b", "scanDepth": 3})
    recent = ["我住杭州", "嗯嗯", "好的"]           # 最后一条是本轮用户这句话
    assert _ids(w, *recent) == ["1"]               # 全局只看最后 2 条;条目 1 自己看 3 条


def test_macros_in_keys_and_content() -> None:
    w = _world({"key": ["{{char}}"], "content": "{{user}}给{{char}}起了名字"})
    [hit] = render(activate(entries_of(w), ["大肥鱼在吗"], S), S)
    assert hit.text == "小鱼给大肥鱼起了名字"
    assert hit.id == "w/0"


def test_render_order_is_ascending_order_value() -> None:
    """order 大的排在后面(更靠近用户这句话),和酒馆相同。"""
    w = _world({"key": ["猫"], "content": "a", "order": 300}, {"key": ["猫"], "content": "b", "order": 10})
    assert _ids(w, "猫") == ["1", "0"]


def test_budget_prefers_high_order_and_stops_once_overflowed() -> None:
    s = LoreSettings(budget_chars=10)
    w = _world({"key": ["猫"], "content": "一" * 8, "order": 200},
               {"key": ["猫"], "content": "二" * 8, "order": 100},
               {"key": ["猫"], "content": "三", "order": 50},
               {"key": ["猫"], "content": "四" * 30, "order": 10, "ignoreBudget": True})
    assert _ids(w, "猫", s=s) == ["3", "0"]


def test_probability() -> None:
    w = _world({"key": ["猫"], "content": "x", "probability": 30})
    assert _ids(w, "猫", roll=0.2) == ["0"]          # 20 ≤ 30
    assert _ids(w, "猫", roll=0.9) == []
    w = _world({"key": ["猫"], "content": "x", "probability": 30, "useProbability": False})
    assert _ids(w, "猫", roll=0.9) == ["0"]


def test_inclusion_group_keeps_one_entry() -> None:
    w = _world({"key": ["天气"], "content": "晴", "group": "天气", "groupWeight": 100},
               {"key": ["天气"], "content": "雨", "group": "天气", "groupWeight": 300})
    assert _ids(w, "天气", roll=0.1) == ["0"]
    assert _ids(w, "天气", roll=0.9) == ["1"]


def test_group_override_wins() -> None:
    w = _world({"key": ["天气"], "content": "晴", "group": "天气"},
               {"key": ["天气"], "content": "雨", "group": "天气", "groupOverride": True})
    assert _ids(w, "天气", roll=0.0) == ["1"]


def test_recursion_off_by_default_and_on_when_enabled() -> None:
    w = _world({"key": ["小鱼"], "content": "小鱼养了一只叫豆豆的猫"}, {"key": ["豆豆"], "content": "豆豆是橘猫"})
    assert _ids(w, "小鱼回来了") == ["0"]
    assert _ids(w, "小鱼回来了", s=LoreSettings(recursive=True)) == ["0", "1"]


def test_recursion_flags() -> None:
    s = LoreSettings(recursive=True)
    w = _world({"key": ["小鱼"], "content": "提到豆豆", "preventRecursion": True},
               {"key": ["豆豆"], "content": "豆豆是橘猫"})
    assert _ids(w, "小鱼", s=s) == ["0"]
    w = _world({"key": ["小鱼"], "content": "提到豆豆"}, {"key": ["豆豆"], "content": "x", "excludeRecursion": True})
    assert _ids(w, "小鱼", s=s) == ["0"]
    w = _world({"key": ["小鱼"], "content": "提到豆豆"}, {"key": ["豆豆"], "content": "x", "delayUntilRecursion": 1})
    assert _ids(w, "豆豆", s=s) == []                 # 只能被递归触发
    assert _ids(w, "小鱼", s=s) == ["0", "1"]


def test_keys_given_as_comma_string_are_split() -> None:
    w = {"w": normalize_world({"entries": [{"key": "猫, 狗", "content": "x"}]})}
    assert w["w"]["entries"]["0"]["key"] == ["猫", "狗"]
    assert _ids(w, "狗") == ["0"]


def test_normalize_keeps_unknown_fields_for_round_trip() -> None:
    raw = {"name": "x", "extensions": {"a": 1},
           "entries": {"7": {"uid": 7, "key": ["猫"], "content": "x", "characterFilter": {"names": ["A"]}}}}
    out = normalize_world(raw)
    assert out["extensions"] == {"a": 1}
    assert out["entries"]["7"]["characterFilter"] == {"names": ["A"]}
    assert out["entries"]["7"]["order"] == 100 and out["entries"]["7"]["selectiveLogic"] == 0
