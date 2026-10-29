"""上游转发:OpenAI 兼容 /chat/completions,支持流式。出错原样返回,不重试、不切换。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

OnDone = Callable[[str, bool], Awaitable[None]]
"""回调(完整回复文本, 是否成功)。"""


@dataclass(frozen=True)
class UpstreamError:
    status_code: int
    body: Any


async def open_stream(client: httpx.AsyncClient, payload: dict[str, Any]) -> httpx.Response | UpstreamError:
    request = client.build_request("POST", "/chat/completions", json=payload)
    try:
        response = await client.send(request, stream=True)
    except httpx.HTTPError as exc:
        return _unreachable(exc)
    if response.status_code != 200:
        raw = await response.aread()
        await response.aclose()
        return UpstreamError(response.status_code, _json_or_text(raw))
    return response


async def relay_stream(response: httpx.Response, on_done: OnDone, notice: str = "") -> AsyncIterator[bytes]:
    """逐行转发上游 SSE,同时累计回复正文。

    notice(降级标记)只给用户看:在带 finish_reason 的块之前插入,不计入回复正文。
    """
    parts: list[str] = []
    ok = False
    pending_notice = notice
    try:
        async for line in response.aiter_lines():
            if not line.startswith("data: "):
                continue
            data = line[6:]
            if data == "[DONE]":
                ok = True
                if pending_notice:
                    yield _notice_chunk(pending_notice)
                yield b"data: [DONE]\n\n"
                break
            chunk = json.loads(data)
            choices = chunk.get("choices", [])
            if pending_notice and any(c.get("finish_reason") for c in choices):
                for c in choices:
                    parts.append((c.get("delta") or {}).get("content") or "")
                yield sse_data({**chunk, "choices": [{**c, "finish_reason": None} for c in choices]})
                yield _notice_chunk(pending_notice)
                pending_notice = ""
                yield sse_data({**chunk, "choices": [{**c, "delta": {}} for c in choices]})
                continue
            for c in choices:
                parts.append((c.get("delta") or {}).get("content") or "")
            yield f"data: {data}\n\n".encode()
    except httpx.HTTPError as exc:
        ok = False
        # 中途断开必须让客户端看到错误,而不是一个"正常结束"的半截回复
        yield sse_data({"error": {"message": f"upstream stream interrupted: {exc}",
                                  "type": "upstream_stream_interrupted"}})
    finally:
        await response.aclose()
        await on_done("".join(parts), ok)


async def complete(client: httpx.AsyncClient, payload: dict[str, Any]) -> tuple[str, dict[str, Any]] | UpstreamError:
    try:
        response = await client.post("/chat/completions", json=payload)
    except httpx.HTTPError as exc:
        return _unreachable(exc)
    if response.status_code != 200:
        return UpstreamError(response.status_code, _json_or_text(response.content))
    body: dict[str, Any] = response.json()
    text = body["choices"][0]["message"].get("content") or ""
    return text, body


def sse_data(obj: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode()


def _notice_chunk(notice: str) -> bytes:
    return sse_data({"object": "chat.completion.chunk",
                     "choices": [{"index": 0, "delta": {"content": notice}, "finish_reason": None}]})


def _unreachable(exc: Exception) -> UpstreamError:
    return UpstreamError(502, {"error": {"message": f"upstream unreachable: {exc}"}})


def _json_or_text(raw: bytes) -> Any:
    try:
        return json.loads(raw)
    except ValueError:
        return {"error": {"message": raw.decode(errors="replace")}}
