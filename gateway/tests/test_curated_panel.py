"""工单 09:策划数据(人设、核心档案、网关世界书)带版本可回滚;世界书按关键词确定性注入;面板单独密码。"""

import json
from pathlib import Path
from typing import Any

import pytest
from conftest import FakeClock, FakeStore, FakeUpstream, Harness, _client
from fastapi.testclient import TestClient

from gateway.stores import MemoryHit

PANEL_PASSWORD = "panel-secret"
H = {"X-Panel": "1"}


def _u(text: str) -> dict[str, Any]:
    return {"role": "user", "content": text}


def _a(text: str) -> dict[str, Any]:
    return {"role": "assistant", "content": text}


def _world(*entries: dict[str, Any]) -> str:
    return json.dumps({"entries": {str(i): {"uid": i, **e} for i, e in enumerate(entries)}}, ensure_ascii=False)


def _write_world(harness: Harness, name: str, *entries: dict[str, Any]) -> None:
    (harness.data_dir / "worlds").mkdir(exist_ok=True)
    (harness.data_dir / "worlds" / f"{name}.json").write_text(_world(*entries), encoding="utf-8")


def _block(harness: Harness) -> str:
    content: str = harness.upstream.last["messages"][-2]["content"]
    return content


def _head(harness: Harness) -> str:
    """第一条 system:人设 / ↑角色定义 / 核心档案 / ↓角色定义。条目默认 position=0(↑角色定义)。"""
    content: str = harness.upstream.last["messages"][0]["content"]
    return content


def _sent(harness: Harness) -> str:
    return "\n".join(str(m.get("content")) for m in harness.upstream.last["messages"])


# --- 世界书注入 ---

def test_keyword_hit_injects_the_entry_after_the_persona(harness: Harness) -> None:
    _write_world(harness, "人物", {"key": ["阿澄"], "content": "阿澄是小鱼的大学室友,在杭州工作。"})

    harness.chat([_u("今天阿澄来找我了")])

    assert _head(harness) == "你是大肥鱼。\n\n阿澄是小鱼的大学室友,在杭州工作。\n\n用户生日:3月14日。"
    assert "阿澄" not in _block(harness)
    assert "lore:人物/0" in harness.audit()[-1]["memory_ids"]


def test_no_keyword_no_injection(harness: Harness) -> None:
    _write_world(harness, "人物", {"key": ["阿澄"], "content": "阿澄是室友"})
    harness.chat([_u("今天天气不错")])
    assert "阿澄" not in _sent(harness)


def test_lore_is_injected_even_on_a_degraded_turn(harness: Harness) -> None:
    _write_world(harness, "人物", {"key": ["阿澄"], "content": "阿澄是室友"})
    harness.facts.fail = True
    harness.affect.fail = True

    harness.chat([_u("阿澄来了")])

    assert "阿澄是室友" in _head(harness)
    assert {"affect", "facts"} <= set(harness.audit()[-1]["degraded_sources"])


def test_earlier_messages_within_scan_depth_also_trigger(harness: Harness) -> None:
    _write_world(harness, "人物", {"key": ["阿澄"], "content": "阿澄是室友"})
    harness.chat([_u("阿澄来了"), _a("真好"), _u("我们去吃饭了")])  # 默认往回看 2 条:不含第一条
    assert "阿澄是室友" not in _head(harness)
    harness.chat([_u("阿澄来了"), _a("阿澄最近好吗"), _u("挺好")])
    assert "阿澄是室友" in _head(harness)


def test_disabled_world_and_lore_settings(harness: Harness) -> None:
    _write_world(harness, "人物", {"key": ["阿澄"], "content": "阿澄是室友"})
    (harness.data_dir / "lore_settings.json").write_text('{"disabled_worlds": ["人物"]}', encoding="utf-8")
    harness.chat([_u("阿澄来了")])
    assert "阿澄是室友" not in _head(harness)

    (harness.data_dir / "lore_settings.json").write_text('{"scan_depth": 3}', encoding="utf-8")
    harness.chat([_u("阿澄来了"), _a("真好"), _u("我们去吃饭了")])
    assert "阿澄是室友" in _head(harness)


def test_broken_world_file_does_not_break_the_turn(harness: Harness) -> None:
    (harness.data_dir / "worlds").mkdir(exist_ok=True)
    (harness.data_dir / "worlds" / "坏的.json").write_text("{不是 JSON", encoding="utf-8")
    _write_world(harness, "好的", {"key": ["猫"], "content": "小鱼养猫"})

    resp = harness.chat([_u("猫呢")])

    assert resp.status_code == 200 and "小鱼养猫" in _head(harness)


def test_entries_at_the_same_position_follow_order(harness: Harness) -> None:
    harness.facts.hits = [MemoryHit("f1", "用户住杭州")]
    _write_world(harness, "w", {"key": ["猫"], "content": "后", "order": 200}, {"key": ["猫"], "content": "前", "order": 5})
    harness.chat([_u("猫")])
    assert "你是大肥鱼。\n\n前\n后\n\n用户生日" in _head(harness)
    assert "用户住杭州" in _block(harness)


def test_tool_rounds_reuse_the_same_lore(harness: Harness) -> None:
    _write_world(harness, "w", {"key": ["天气"], "content": "小鱼在新余"})
    call = {"id": "c1", "type": "function", "function": {"name": "web_search", "arguments": "{}"}}
    harness.upstream.script = [{"tool_calls": [call]}, {"content": "晴"}]
    ask = [_u("明天天气")]
    harness.chat(ask)
    first = harness.upstream.last["messages"]
    harness.chat([*ask, {"role": "assistant", "content": None, "tool_calls": [call]},
                  {"role": "tool", "tool_call_id": "c1", "content": "晴"}])
    assert harness.upstream.last["messages"][: len(first)] == first


# --- 人设、核心档案:直接改文件也生效,并且留版本 ---

def test_persona_file_edits_take_effect_next_turn(harness: Harness) -> None:
    harness.chat([_u("在吗")])
    (harness.data_dir / "persona.md").write_text("你是大肥鱼,说话软糯。", encoding="utf-8")
    harness.chat([_u("在吗")])
    assert harness.upstream.last["messages"][0]["content"].startswith("你是大肥鱼,说话软糯。")


# --- 面板 ---

@pytest.fixture
def panel(tmp_path: Path) -> Any:
    (tmp_path / "persona.md").write_text("你是大肥鱼。", encoding="utf-8")
    upstream, facts, affect, clock = FakeUpstream(), FakeStore(), FakeStore(), FakeClock()
    with _client(tmp_path, upstream, facts, affect, clock, panel_password=PANEL_PASSWORD) as client:
        client.base_url = "http://testserver"
        yield client, tmp_path, upstream


def _login(client: TestClient) -> None:
    assert client.post("/panel/api/login", json={"password": PANEL_PASSWORD}).status_code == 200


def test_panel_does_not_exist_without_a_password(harness: Harness) -> None:
    assert harness.client.get("/panel").status_code == 404
    assert harness.client.get("/panel/api/state").status_code == 404


def test_panel_requires_its_own_password_not_a_device_token(panel: Any) -> None:
    client, _, _ = panel
    assert client.get("/panel").status_code == 200                     # 页面本身可以打开(只有登录框)
    assert client.get("/panel/api/state").status_code == 401
    assert client.get("/panel/api/state", headers={"Authorization": "Bearer tok-pc"}).status_code == 401
    assert client.post("/panel/api/login", json={"password": "tok-pc"}).status_code == 401
    _login(client)
    assert client.get("/panel/api/state").status_code == 200


def test_session_cookie_is_httponly_and_strict(panel: Any) -> None:
    client, _, _ = panel
    resp = client.post("/panel/api/login", json={"password": PANEL_PASSWORD})
    cookie = resp.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie


def test_forged_or_expired_cookie_is_rejected(panel: Any) -> None:
    client, _, _ = panel
    client.cookies.set("dafeiyu_panel", "9999999999.deadbeef", path="/panel")
    assert client.get("/panel/api/state").status_code == 401
    client.cookies.set("dafeiyu_panel", "1.abc", path="/panel")
    assert client.get("/panel/api/state").status_code == 401


def test_writes_need_the_x_panel_header(panel: Any) -> None:
    client, _, _ = panel
    _login(client)
    assert client.put("/panel/api/doc?kind=persona", json={"content": "x"}).status_code == 403
    assert client.put("/panel/api/doc?kind=persona", json={"content": "x"}, headers=H).status_code == 200


def test_edit_persona_and_roll_back(panel: Any) -> None:
    client, data_dir, upstream = panel
    _login(client)
    client.put("/panel/api/doc?kind=persona", json={"content": "你是大肥鱼,爱吃小鱼干。"}, headers=H)
    assert (data_dir / "persona.md").read_text(encoding="utf-8") == "你是大肥鱼,爱吃小鱼干。"

    versions = client.get("/panel/api/history?kind=persona").json()["versions"]
    assert [v["source"] for v in versions] == ["panel", "initial"]
    original = versions[-1]["id"]
    assert client.get(f"/panel/api/version?id={original}").json()["content"] == "你是大肥鱼。"

    client.post(f"/panel/api/rollback?kind=persona&id={original}", headers=H)
    assert (data_dir / "persona.md").read_text(encoding="utf-8") == "你是大肥鱼。"
    sources = [v["source"] for v in client.get("/panel/api/history?kind=persona").json()["versions"]]
    assert sources == [f"rollback:{original}", "panel", "initial"]


def test_saving_the_same_content_does_not_add_a_version(panel: Any) -> None:
    client, _, _ = panel
    _login(client)
    for _ in range(3):
        client.put("/panel/api/doc?kind=profile", json={"content": "用户叫小鱼"}, headers=H)
    assert len(client.get("/panel/api/history?kind=profile").json()["versions"]) == 1


def test_edits_made_outside_the_panel_are_versioned_too(panel: Any) -> None:
    client, data_dir, _ = panel
    _login(client)
    client.put("/panel/api/doc?kind=persona", json={"content": "A"}, headers=H)
    (data_dir / "persona.md").write_text("B", encoding="utf-8")   # 用记事本改的
    client.post("/v1/chat/completions", json={"model": "dafeiyu", "messages": [_u("在吗")]},
                headers={"Authorization": "Bearer tok-pc"})
    sources = [v["source"] for v in client.get("/panel/api/history?kind=persona").json()["versions"]]
    assert sources[0] == "external"


def test_world_crud_round_trips_sillytavern_format(panel: Any) -> None:
    client, data_dir, _ = panel
    _login(client)
    st_export = {"entries": {"5": {"uid": 5, "key": ["阿澄"], "keysecondary": [], "content": "室友",
                                   "characterFilter": {"names": [], "tags": [], "isExclude": False},
                                   "selectiveLogic": None, "position": 4, "depth": 2}}}
    assert client.put("/panel/api/doc?kind=world&name=人物", json={"content": json.dumps(st_export)},
                      headers=H).status_code == 200

    saved = json.loads(client.get("/panel/api/doc?kind=world&name=人物").json()["content"])
    entry = saved["entries"]["5"]
    assert entry["characterFilter"] == st_export["entries"]["5"]["characterFilter"]   # 酒馆字段不丢
    assert entry["position"] == 4 and entry["order"] == 100 and entry["probability"] == 100
    assert client.get("/panel/api/state").json()["worlds"] == [
        {"name": "人物", "enabled": True, "entries": 1, "broken": False}]

    client.delete("/panel/api/doc?kind=world&name=人物", headers=H)
    assert not (data_dir / "worlds" / "人物.json").exists()
    [deleted, created] = client.get("/panel/api/history?kind=world&name=人物").json()["versions"]
    assert deleted["deleted"]
    client.post(f"/panel/api/rollback?kind=world&name=人物&id={created['id']}", headers=H)
    assert (data_dir / "worlds" / "人物.json").exists()


@pytest.mark.parametrize("name", ["../persona", "a/b", "", ".hidden", "x" * 81])
def test_world_names_cannot_escape_the_worlds_folder(panel: Any, name: str) -> None:
    client, _, _ = panel
    _login(client)
    resp = client.put("/panel/api/doc", params={"kind": "world", "name": name}, json={"content": "{}"}, headers=H)
    assert resp.status_code == 400


def test_invalid_json_is_rejected_and_file_untouched(panel: Any) -> None:
    client, data_dir, _ = panel
    _login(client)
    client.put("/panel/api/doc?kind=world&name=w", json={"content": _world({"key": ["a"], "content": "b"})}, headers=H)
    before = (data_dir / "worlds" / "w.json").read_text(encoding="utf-8")
    assert client.put("/panel/api/doc?kind=world&name=w", json={"content": "{oops"}, headers=H).status_code == 400
    assert (data_dir / "worlds" / "w.json").read_text(encoding="utf-8") == before


def test_lore_test_shows_what_would_be_injected(panel: Any) -> None:
    client, _, _ = panel
    _login(client)
    client.put("/panel/api/doc?kind=world&name=w", headers=H,
               json={"content": _world({"key": ["阿澄"], "content": "{{user}}的室友", "comment": "阿澄"},
                                       {"key": [], "constant": True, "content": "常驻"})})
    client.put("/panel/api/doc?kind=settings", headers=H, json={"content": '{"user_name": "小鱼"}'})

    got = client.post("/panel/api/lore-test", json={"messages": ["阿澄来了"]}).json()["entries"]

    assert [(e["comment"], e["text"], e["constant"]) for e in got] == [("阿澄", "小鱼的室友", False), ("", "常驻", True)]


def test_panel_edits_reach_the_next_turn(panel: Any) -> None:
    client, _, upstream = panel
    _login(client)
    client.put("/panel/api/doc?kind=world&name=w", headers=H,
               json={"content": _world({"key": ["阿澄"], "content": "阿澄是室友"})})
    client.post("/v1/chat/completions", json={"model": "dafeiyu", "messages": [_u("阿澄来了")]},
                headers={"Authorization": "Bearer tok-pc"})
    assert "阿澄是室友" in upstream.last["messages"][0]["content"]


def test_preview_shows_where_persona_profile_and_entries_land(panel: Any) -> None:
    client, data_dir, _ = panel
    _login(client)
    (data_dir / "core_profile.md").write_text("用户叫小鱼", encoding="utf-8")
    client.put("/panel/api/doc?kind=world&name=w", headers=H,
               json={"content": _world({"key": ["阿澄"], "content": "上", "comment": "上"},
                                       {"key": ["阿澄"], "content": "深二", "position": 4, "depth": 2, "role": 1})})

    r = client.post("/panel/api/lore-test", json={"messages": ["在吗", "在呢", "阿澄来了"]}).json()

    assert [(m["role"], m["depth"], m["kind"]) for m in r["layout"]] == [
        ("system", None, "系统提示开头:人设 → 世界书 ↑角色定义 → 核心档案"),
        ("user", 2, "历史"),
        ("user", 2, "世界书 @D2"),        # 深度 2:插在倒数第 2 条真实消息(「在呢」)之前
        ("assistant", 1, "历史"),
        ("system", 1, "召回块"),           # 深度 1:紧挨在用户这句话之前
        ("user", 0, "本轮用户这句话")]
    assert r["layout"][0]["content"] == "你是大肥鱼。\n\n上\n\n用户叫小鱼"
    assert [e["where"] for e in r["entries"]] == ["↑角色定义", "@D2 用户"]


def test_state_exposes_sillytavern_entry_defaults_for_new_entries(panel: Any) -> None:
    """面板新建条目用酒馆的默认值(组权重 100、顺序 100、概率 100、插入深度 4),不是 0。"""
    client, _, _ = panel
    _login(client)
    d = client.get("/panel/api/state").json()["entry_defaults"]
    assert (d["groupWeight"], d["order"], d["probability"], d["depth"], d["scanDepth"]) == (100, 100, 100, 4, None)
