"""面板批量管理事实库记忆:多选 / 全选后一次失效或恢复(用 Hindsight 的可恢复失效,不是删除)。"""

from pathlib import Path
from typing import Any

import pytest
from conftest import FakeClock, FakeStore, FakeUpstream, _client

H = {"X-Panel": "1"}


class CuratableFacts(FakeStore):
    def __init__(self) -> None:
        super().__init__()
        self.mem: dict[str, dict[str, Any]] = {
            "w1": {"id": "w1", "text": "用户住杭州", "fact_type": "world", "state": "valid"},
            "w2": {"id": "w2", "text": "用户养猫", "fact_type": "world", "state": "valid"},
            "e1": {"id": "e1", "text": "大肥鱼查了天气", "fact_type": "experience", "state": "valid"},
            "o1": {"id": "o1", "text": "用户喜欢猫", "fact_type": "observation", "state": "valid"},
        }
        self.calls: list[dict[str, Any]] = []

    async def list_memories(self, *, q: str = "", fact_type: str = "", state: str = "valid",
                            limit: int = 50, offset: int = 0) -> dict[str, Any]:
        self.calls.append({"q": q, "type": fact_type, "state": state, "limit": limit, "offset": offset})
        rows = [m for m in self.mem.values() if m["state"] == state
                and (not fact_type or m["fact_type"] == fact_type) and q in m["text"]]
        return {"items": rows[offset: offset + limit], "total": len(rows)}

    async def set_memory_state(self, memory_id: str, state: str, reason: str = "") -> None:
        m = self.mem.get(memory_id)
        if m is None:
            raise ValueError("not found")
        if m["fact_type"] == "observation":
            raise ValueError("observation 不能直接改")
        m["state"] = state


@pytest.fixture
def panel(tmp_path: Path) -> Any:
    facts = CuratableFacts()
    with _client(tmp_path, FakeUpstream(), facts, FakeStore(), FakeClock(), panel_password="pw") as client:
        assert client.post("/panel/api/login", json={"password": "pw"}).status_code == 200
        yield client, facts


def test_list_filters_and_paginates(panel: Any) -> None:
    client, facts = panel
    r = client.get("/panel/api/memories", params={"type": "world", "q": "猫", "limit": 10, "offset": 0}).json()
    assert r["total"] == 1 and [m["id"] for m in r["items"]] == ["w2"]
    assert facts.calls[-1] == {"q": "猫", "type": "world", "state": "valid", "limit": 10, "offset": 0}
    assert client.get("/panel/api/memories", params={"limit": 9999}).status_code == 200
    assert facts.calls[-1]["limit"] == 200


def test_bulk_invalidate_then_restore(panel: Any) -> None:
    client, facts = panel
    r = client.post("/panel/api/memories/state", json={"ids": ["w1", "w2", "e1"], "state": "invalidated"}, headers=H).json()
    assert r == {"ok": True, "done": ["w1", "w2", "e1"], "failed": []}
    left = client.get("/panel/api/memories").json()
    assert [m["id"] for m in left["items"]] == ["o1"]
    gone = client.get("/panel/api/memories", params={"state": "invalidated"}).json()
    assert {m["id"] for m in gone["items"]} == {"w1", "w2", "e1"}

    client.post("/panel/api/memories/state", json={"ids": ["w1"], "state": "valid"}, headers=H)
    assert facts.mem["w1"]["state"] == "valid"


def test_partial_failures_are_reported_not_fatal(panel: Any) -> None:
    client, facts = panel
    r = client.post("/panel/api/memories/state", json={"ids": ["w1", "o1", "nope"], "state": "invalidated"},
                    headers=H).json()
    assert r["ok"] is False and r["done"] == ["w1"]
    assert [f["id"] for f in r["failed"]] == ["o1", "nope"]
    assert facts.mem["w1"]["state"] == "invalidated"


def test_bulk_needs_login_header_and_valid_input(panel: Any) -> None:
    client, _ = panel
    assert client.post("/panel/api/memories/state", json={"ids": ["w1"], "state": "invalidated"}).status_code == 403
    assert client.post("/panel/api/memories/state", json={"ids": [], "state": "invalidated"}, headers=H).status_code == 400
    assert client.post("/panel/api/memories/state", json={"ids": ["w1"], "state": "deleted"}, headers=H).status_code == 400
    assert client.get("/panel/api/memories", params={"state": "x"}).status_code == 400
    client.post("/panel/api/logout")
    client.cookies.clear()
    assert client.get("/panel/api/memories").status_code == 401


def test_store_without_curation_support_hides_the_feature(tmp_path: Path) -> None:
    with _client(tmp_path, FakeUpstream(), FakeStore(), FakeStore(), FakeClock(), panel_password="pw") as client:
        client.post("/panel/api/login", json={"password": "pw"})
        assert client.get("/panel/api/memories").status_code == 404
