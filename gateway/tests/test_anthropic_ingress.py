"""工单 02:Anthropic 格式入口 /v1/messages,与 OpenAI 入口走同一条记忆链路(ADR-0002)。"""

import json
from typing import Any

import httpx
from conftest import PC_TOKEN, Harness

from gateway.stores import MemoryHit


def _messages(harness: Harness, body: dict[str, Any], token: str = PC_TOKEN) -> httpx.Response:
    resp: httpx.Response = harness.client.post(
        "/v1/messages", json={"model": "dafeiyu", "max_tokens": 512, **body},
        headers={"x-api-key": token, "anthropic-version": "2023-06-01"})
    return resp


def test_anthropic_request_reaches_upstream_like_an_openai_one(harness: Harness) -> None:
    harness.facts.hits = [MemoryHit("f1", "用户喜欢吃辣")]

    resp = _messages(harness, {
        "system": "Kelivo 的提示词",
        "messages": [{"role": "user", "content": [{"type": "text", "text": "晚饭吃什么"}]}],
    })

    assert resp.status_code == 200
    sent = harness.upstream.last
    assert sent["messages"][0]["content"].startswith("你是大肥鱼。")
    assert "用户喜欢吃辣" in sent["messages"][-2]["content"]
    assert sent["messages"][-1] == {"role": "user", "content": "晚饭吃什么"}
    assert sent["max_tokens"] == 512


def test_anthropic_reply_comes_back_in_anthropic_shape(harness: Harness) -> None:
    harness.upstream.reply = "吃火锅吧"

    body = _messages(harness, {"messages": [{"role": "user", "content": "晚饭吃什么"}]}).json()

    assert body["type"] == "message"
    assert body["role"] == "assistant"
    assert body["content"] == [{"type": "text", "text": "吃火锅吧"}]
    assert body["stop_reason"] == "end_turn"


def test_anthropic_streaming_emits_anthropic_events(harness: Harness) -> None:
    harness.upstream.reply = "吃火锅吧"

    resp = _messages(harness, {"stream": True, "messages": [{"role": "user", "content": "晚饭吃什么"}]})

    events = [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: ")]
    kinds = [e["type"] for e in events]
    assert kinds[0] == "message_start" and kinds[-1] == "message_stop"
    text = "".join(e["delta"]["text"] for e in events if e["type"] == "content_block_delta")
    assert text == "吃火锅吧"
    assert any(e["type"] == "message_delta" and e["delta"]["stop_reason"] == "end_turn" for e in events)


def test_anthropic_ingress_rejects_unknown_key(harness: Harness) -> None:
    resp = _messages(harness, {"messages": [{"role": "user", "content": "嗨"}]}, token="tok-stranger")

    assert resp.status_code == 401
    assert resp.json()["type"] == "error"


def test_anthropic_upstream_error_comes_back_as_anthropic_error(harness: Harness) -> None:
    harness.upstream.fail_status = 429

    resp = _messages(harness, {"messages": [{"role": "user", "content": "嗨"}]})

    assert resp.status_code == 429
    assert resp.json()["type"] == "error"
