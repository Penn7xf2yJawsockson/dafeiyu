"""工单 01:网关透明转发与人设替换。"""

import json

from conftest import Harness


def test_unknown_token_is_rejected(harness: Harness) -> None:
    resp = harness.chat([{"role": "user", "content": "你好"}], token="tok-stranger")

    assert resp.status_code == 401
    assert harness.upstream.requests == []


def test_reply_is_returned_from_upstream(harness: Harness) -> None:
    harness.upstream.reply = "晚上好呀"

    resp = harness.chat([{"role": "user", "content": "晚上好"}])

    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "晚上好呀"


def test_kelivo_system_prompt_is_replaced_by_persona_and_core_profile(harness: Harness) -> None:
    harness.chat([
        {"role": "system", "content": "Kelivo 里残留的提示词"},
        {"role": "user", "content": "你是谁"},
    ])

    sent = harness.upstream.last
    assert sent["model"] == "deepseek-flash"
    assert sent["messages"][0] == {"role": "system", "content": "你是大肥鱼。\n\n用户生日:3月14日。"}
    assert all("残留" not in str(m["content"]) for m in sent["messages"])


def _stream_text(resp_text: str) -> str:
    out = []
    for line in resp_text.splitlines():
        if line.startswith("data: ") and line != "data: [DONE]":
            for choice in json.loads(line[6:])["choices"]:
                out.append(choice["delta"].get("content") or "")
    return "".join(out)


def test_streaming_reply_arrives_in_full(harness: Harness) -> None:
    harness.upstream.reply = "今天也辛苦啦"

    resp = harness.chat([{"role": "user", "content": "下班了"}], stream=True)

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert _stream_text(resp.text) == "今天也辛苦啦"
    assert resp.text.rstrip().endswith("data: [DONE]")


def test_every_turn_is_written_to_transcript_log(harness: Harness) -> None:
    harness.upstream.reply = "收到"

    harness.chat([{"role": "user", "content": "记一下"}], stream=True)

    [turn] = harness.turns()
    assert turn["device"] == "pc"
    assert turn["companion"] == "dafeiyu"
    assert turn["user_text"] == "记一下"
    assert turn["assistant_text"] == "收到"
    assert turn["status"] == "pending"


def test_upstream_error_is_returned_and_audited_as_failed(harness: Harness) -> None:
    harness.upstream.fail_status = 503

    resp = harness.chat([{"role": "user", "content": "在吗"}])

    assert resp.status_code == 503
    [entry] = harness.audit()
    assert entry["outcome"] == "failed"
    assert entry["device"] == "pc"
    assert harness.turns()[0]["status"] == "failed"


def test_admin_endpoints_require_admin_token(harness: Harness) -> None:
    assert harness.client.get("/admin/turns").status_code == 401
    assert harness.client.get("/admin/turns", headers={"Authorization": "Bearer tok-pc"}).status_code == 401
