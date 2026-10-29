"""抓包开关:把 Kelivo 发来的原始请求和网关发给上游的请求存到本机,用来确认 Kelivo 的实际格式。"""

import json
from pathlib import Path

from conftest import PC_TOKEN, FakeClock, FakeStore, FakeUpstream, Harness, _client


def test_capture_is_off_by_default(harness: Harness) -> None:
    harness.chat([{"role": "user", "content": "嗨"}])

    assert not (harness.data_dir / "debug").exists()


def test_capture_keeps_the_last_n_requests_without_tokens(tmp_path: Path) -> None:
    (tmp_path / "persona.md").write_text("你是大肥鱼。", encoding="utf-8")
    with _client(tmp_path, FakeUpstream(), FakeStore(), FakeStore(), FakeClock(), debug_capture=2) as client:
        for text in ("一", "二", "三"):
            client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {PC_TOKEN}"},
                        json={"model": "dafeiyu", "messages": [{"role": "system", "content": "Kelivo 系统消息"},
                                                               {"role": "user", "content": text}]})

    files = sorted((tmp_path / "debug").glob("*.json"))
    assert len(files) == 2
    latest = json.loads(files[-1].read_text(encoding="utf-8"))
    assert latest["incoming"]["body"]["messages"][0]["content"] == "Kelivo 系统消息"
    assert latest["incoming"]["body"]["messages"][-1]["content"] == "三"
    assert latest["outgoing"]["messages"][0]["content"].startswith("你是大肥鱼。")
    assert all(PC_TOKEN not in f.read_text(encoding="utf-8") for f in files)
    assert all("UPSTREAM_KEY" not in f.read_text(encoding="utf-8") for f in files)
