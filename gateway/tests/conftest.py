"""测试替身:假上游、假事实库、假情感库、可控时钟。

测试只从网关 HTTP 接口进出(规格 Testing Decisions 的主要切入点)。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from gateway.app import create_app
from gateway.config import Settings
from gateway.stores import MemoryHit

PC_TOKEN = "tok-pc"
PHONE_TOKEN = "tok-phone"
ADMIN_TOKEN = "tok-admin"
ADMIN = {"Authorization": f"Bearer {ADMIN_TOKEN}"}


class FakeClock:
    def __init__(self) -> None:
        self._now = datetime(2026, 10, 1, 20, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._now

    def advance(self, **kwargs: float) -> None:
        self._now += timedelta(**kwargs)


@dataclass
class FakeUpstream:
    """记录收到的请求,按脚本返回回复。

    script 里每一项是一次回复:{"content": "..."} 或 {"tool_calls": [...]};脚本用完后回复 reply。
    """

    reply: str = "好的"
    fail_status: int | None = None
    fail_body: Any = None
    break_midstream: bool = False
    script: list[dict[str, Any]] = field(default_factory=list)
    requests: list[dict[str, Any]] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        if self.fail_status is not None:
            error = self.fail_body if self.fail_body is not None else {"error": {"message": "上游故障"}}
            return httpx.Response(self.fail_status, json=error)
        step = self.script.pop(0) if self.script else {"content": self.reply}
        if body.get("stream"):
            headers = {"content-type": "text/event-stream"}
            if self.break_midstream:
                return httpx.Response(200, stream=_BrokenStream(_sse(step)), headers=headers)
            return httpx.Response(200, content=_sse(step), headers=headers)
        return httpx.Response(200, json=_completion(step))

    @property
    def last(self) -> dict[str, Any]:
        return self.requests[-1]


def _completion(step: dict[str, Any]) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": step.get("content")}
    if step.get("tool_calls"):
        message["tool_calls"] = step["tool_calls"]
    return {
        "id": "cmpl-1",
        "object": "chat.completion",
        "model": "deepseek-flash",
        "choices": [{"index": 0, "message": message,
                     "finish_reason": "tool_calls" if step.get("tool_calls") else "stop"}],
    }


def _sse(step: dict[str, Any]) -> bytes:
    chunks: list[dict[str, Any]] = []
    if step.get("tool_calls"):
        for i, call in enumerate(step["tool_calls"]):
            head = {"index": i, "id": call["id"], "type": "function",
                    "function": {"name": call["function"]["name"], "arguments": ""}}
            chunks.append({"choices": [{"index": 0, "delta": {"role": "assistant", "tool_calls": [head]},
                                        "finish_reason": None}]})
            chunks.append({"choices": [{"index": 0, "delta": {"tool_calls": [
                {"index": i, "function": {"arguments": call["function"]["arguments"]}}]}, "finish_reason": None}]})
        finish = "tool_calls"
    else:
        text = step.get("content") or ""
        for piece in [text[: len(text) // 2], text[len(text) // 2 :]]:
            chunks.append({"choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]})
        finish = "stop"
    chunks.append({"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]})
    lines = [f"data: {json.dumps({'id': 'c1', 'object': 'chat.completion.chunk', **c}, ensure_ascii=False)}\n\n"
             for c in chunks]
    return ("".join(lines) + "data: [DONE]\n\n").encode()


class _BrokenStream(httpx.AsyncByteStream):
    """只发出第一个增量块,然后连接中断。"""

    def __init__(self, full: bytes) -> None:
        self._first = full.split(b"\n\n")[0] + b"\n\n"

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self._first
        raise httpx.ReadError("连接中断")


@dataclass
class FakeStore:
    """事实库或情感库的替身:记录写入,按脚本返回召回。"""

    hits: list[MemoryHit] = field(default_factory=list)
    surfaced: list[MemoryHit] = field(default_factory=list)
    fail: bool = False
    hang_seconds: float = 0.0
    writes: list[dict[str, Any]] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)

    async def recall(self, query: str) -> list[MemoryHit]:
        await self._maybe_misbehave()
        self.queries.append(query)
        return list(self.hits)

    async def surface(self) -> list[MemoryHit]:
        await self._maybe_misbehave()
        return list(self.surfaced)

    async def write_episode(self, episode_id: str, transcript: str) -> None:
        if self.fail:
            raise RuntimeError("写入失败")
        self.writes.append({"episode_id": episode_id, "transcript": transcript})

    async def _maybe_misbehave(self) -> None:
        if self.hang_seconds:
            import asyncio

            await asyncio.sleep(self.hang_seconds)
        if self.fail:
            raise RuntimeError("记忆库不可用")


@dataclass
class Harness:
    client: TestClient
    upstream: FakeUpstream
    facts: FakeStore
    affect: FakeStore
    clock: FakeClock
    data_dir: Path

    def chat(self, messages: list[dict[str, Any]], token: str = PC_TOKEN, **extra: Any) -> httpx.Response:
        body = {"model": "dafeiyu", "messages": messages, **extra}
        resp: httpx.Response = self.client.post(
            "/v1/chat/completions", json=body, headers={"Authorization": f"Bearer {token}"})
        return resp

    def restart(self) -> None:
        """模拟网关重启:同一数据目录、同一组外部服务,全新进程状态。"""
        self.client.__exit__(None, None, None)
        self.client = _client(self.data_dir, self.upstream, self.facts, self.affect, self.clock).__enter__()

    def tick(self) -> None:
        """推进后台工作:结束空闲的段、投递写入队列。"""
        resp = self.client.post("/admin/tick", headers=ADMIN)
        assert resp.status_code == 200, resp.text

    def turns(self) -> list[dict[str, Any]]:
        resp = self.client.get("/admin/turns", headers=ADMIN)
        assert resp.status_code == 200, resp.text
        turns: list[dict[str, Any]] = resp.json()["turns"]
        return turns

    def audit(self) -> list[dict[str, Any]]:
        resp = self.client.get("/admin/audit", headers=ADMIN)
        assert resp.status_code == 200, resp.text
        entries: list[dict[str, Any]] = resp.json()["entries"]
        return entries


def _client(data_dir: Path, upstream: FakeUpstream, facts: FakeStore, affect: FakeStore,
            clock: FakeClock, **overrides: Any) -> TestClient:
    settings = Settings(**{
        "data_dir": data_dir,
        "devices": {PC_TOKEN: "pc", PHONE_TOKEN: "phone"},
        "upstream_base_url": "https://upstream.test/v1",
        "upstream_api_key": "UPSTREAM_KEY",
        "upstream_model": "deepseek-flash",
        "admin_token": ADMIN_TOKEN,
        "recall_timeout_seconds": 0.2,
        "background_worker": False,  # 测试里只通过 tick 推进,保证确定性
        **overrides,
    })
    app = create_app(settings, upstream_transport=httpx.MockTransport(upstream.handler),
                     fact_store=facts, affect_store=affect, clock=clock)
    return TestClient(app)


@pytest.fixture
def harness(tmp_path: Path) -> Iterator[Harness]:
    upstream, facts, affect, clock = FakeUpstream(), FakeStore(), FakeStore(), FakeClock()
    (tmp_path / "persona.md").write_text("你是大肥鱼。", encoding="utf-8")
    (tmp_path / "core_profile.md").write_text("用户生日:3月14日。", encoding="utf-8")
    h = Harness(_client(tmp_path, upstream, facts, affect, clock).__enter__(), upstream, facts, affect, clock, tmp_path)
    yield h
    h.client.__exit__(None, None, None)
