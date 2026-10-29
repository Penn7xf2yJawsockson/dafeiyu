"""Anthropic Messages 格式 ⇄ 网关内部使用的 OpenAI Chat Completions 格式。

只转换文本与图片;tool_use / thinking 等块不在本项目范围内,会被丢弃。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

_STOP = {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use", "content_filter": "end_turn"}


def _openai_content(content: Any) -> Any:
    if isinstance(content, str):
        return content
    parts: list[dict[str, Any]] = []
    for block in content or []:
        if block.get("type") == "text":
            parts.append({"type": "text", "text": block["text"]})
        elif block.get("type") == "image" and block.get("source", {}).get("type") == "base64":
            src = block["source"]
            parts.append({"type": "image_url", "image_url": {"url": f"data:{src['media_type']};base64,{src['data']}"}})
    if all(p["type"] == "text" for p in parts):
        return "".join(p["text"] for p in parts)
    return parts


def to_openai(body: dict[str, Any]) -> dict[str, Any]:
    """system 字段直接丢弃:网关总是用自己的人设(ADR-0002、CONTEXT.md「人设」)。"""
    out: dict[str, Any] = {
        "model": body.get("model", ""),
        "messages": [{"role": m["role"], "content": _openai_content(m.get("content"))} for m in body["messages"]],
        "stream": bool(body.get("stream")),
    }
    for src, dst in (("max_tokens", "max_tokens"), ("temperature", "temperature"), ("top_p", "top_p"),
                     ("stop_sequences", "stop")):
        if src in body:
            out[dst] = body[src]
    return out


def from_openai(raw: dict[str, Any], model: str) -> dict[str, Any]:
    choice = raw["choices"][0]
    return {
        "id": f"msg_{uuid.uuid4().hex}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": choice["message"].get("content") or ""}],
        "stop_reason": _STOP.get(choice.get("finish_reason") or "stop", "end_turn"),
        "stop_sequence": None,
        "usage": {"input_tokens": (raw.get("usage") or {}).get("prompt_tokens", 0),
                  "output_tokens": (raw.get("usage") or {}).get("completion_tokens", 0)},
    }


def _message(body: Any) -> str:
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        return str(err.get("message", err))
    if isinstance(err, str):
        return err
    return str(body)


def error(status: int, body: Any) -> dict[str, Any]:
    message = _message(body)
    kind = {401: "authentication_error", 429: "rate_limit_error", 400: "invalid_request_error"}.get(status, "api_error")
    return {"type": "error", "error": {"type": kind, "message": message}}


def _event(name: str, data: dict[str, Any]) -> bytes:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


async def stream_from_openai(chunks: AsyncIterator[bytes], model: str) -> AsyncIterator[bytes]:
    """把网关转发出的 OpenAI SSE 字节流改写成 Anthropic 事件流。"""
    yield _event("message_start", {"type": "message_start", "message": {
        "id": f"msg_{uuid.uuid4().hex}", "type": "message", "role": "assistant", "model": model,
        "content": [], "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 0, "output_tokens": 0}}})
    yield _event("content_block_start", {"type": "content_block_start", "index": 0,
                                         "content_block": {"type": "text", "text": ""}})
    stop_reason = "end_turn"
    async for raw in chunks:
        for line in raw.decode().splitlines():
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            data = json.loads(line[6:])
            if "error" in data:  # 上游中途断开:发 error 事件,不发 message_stop
                yield _event("error", {"type": "error",
                                       "error": {"type": "api_error", "message": _message(data)}})
                return
            for choice in data.get("choices", []):
                text = (choice.get("delta") or {}).get("content")
                if text:
                    yield _event("content_block_delta", {"type": "content_block_delta", "index": 0,
                                                         "delta": {"type": "text_delta", "text": text}})
                if choice.get("finish_reason"):
                    stop_reason = _STOP.get(choice["finish_reason"], "end_turn")
    yield _event("content_block_stop", {"type": "content_block_stop", "index": 0})
    yield _event("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop_reason,
                                                                      "stop_sequence": None},
                                   "usage": {"output_tokens": 0}})
    yield _event("message_stop", {"type": "message_stop"})
