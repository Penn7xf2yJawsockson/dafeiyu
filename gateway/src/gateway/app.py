"""网关 HTTP 入口。"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
import time
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any, Protocol

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from gateway import anthropic
from gateway import upstream as up
from gateway.compose import compose
from gateway.config import Settings
from gateway.lifecycle import Lifecycle, context_hash, message_text
from gateway.recall import recall
from gateway.stores import MemoryStore
from gateway.transcript import FAILED, TranscriptLog

DEGRADED_NOTICE = "\n\n⚠ 记忆不完整"
logger = logging.getLogger("gateway")

TurnResult = up.UpstreamError | dict[str, Any] | AsyncIterator[bytes]
"""失败 / 非流式的完整回复 / 流式的 OpenAI SSE 字节流。"""


class Clock(Protocol):
    def now(self) -> datetime: ...


def _read(settings: Settings, name: str) -> str:
    path = settings.data_dir / name
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def _bearer(request: Request) -> str:
    return request.headers.get("authorization", "").removeprefix("Bearer ").strip()


def _unauthorized() -> JSONResponse:
    return JSONResponse({"error": {"message": "unknown gateway token"}}, status_code=401)


def create_app(
    settings: Settings,
    *,
    upstream_transport: httpx.AsyncBaseTransport | None = None,
    fact_store: MemoryStore,
    affect_store: MemoryStore,
    clock: Clock,
) -> FastAPI:
    log = TranscriptLog(settings.data_dir / "transcript.sqlite3")
    life = Lifecycle(settings, log, fact_store, affect_store, notice=DEGRADED_NOTICE)
    drain_lock = asyncio.Lock()
    client = httpx.AsyncClient(
        base_url=settings.upstream_base_url,
        transport=upstream_transport,
        timeout=settings.upstream_timeout_seconds,
        headers={"Authorization": f"Bearer {settings.upstream_api_key}"},
    )

    background: set[asyncio.Task[None]] = set()

    async def tick() -> None:
        async with drain_lock:
            life.sweep_idle(clock.now())
            await life.drain()

    def tick_soon() -> None:
        """立刻在后台推进写入队列:刚确定的轮尽早写进事实库,下一轮就能查到。"""
        if settings.background_worker:
            task = asyncio.create_task(tick())
            background.add(task)
            task.add_done_callback(background.discard)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        task: asyncio.Task[None] | None = None
        if settings.background_worker:
            async def loop() -> None:
                while True:
                    try:
                        await tick()
                    except Exception:
                        logger.exception("后台写入/扫描失败,下一轮重试")
                    await asyncio.sleep(settings.background_interval_seconds)
            task = asyncio.create_task(loop())
        yield
        if task:
            task.cancel()
        await client.aclose()

    app = FastAPI(title="dafeiyu-gateway", lifespan=lifespan)

    async def run_turn(device: str, body: dict[str, Any]) -> TurnResult:
        """一轮的完整处理。body 是 OpenAI Chat Completions 格式。

        从 resolve_previous 到 open_turn 之间没有 await,在单线程事件循环里天然不会与
        其他请求或后台扫描交错;改动这一段时不要在中间插入 await。
        """
        started = time.monotonic()
        history = [m for m in body.get("messages") or [] if m.get("role") != "system"]
        if not history:
            return up.UpstreamError(400, {"error": {"message": "messages must contain at least one message"}})
        companion, now = settings.companion, clock.now()

        superseded = life.resolve_previous(device, companion, history)
        episode_id, first_turn = life.episode_for(device, companion, now)
        handoff = life.handoff(device, companion, now) if first_turn else []
        turn = log.open_turn(device=device, companion=companion, episode_id=episode_id, created_at=now,
                             context_hash=context_hash(history[:-1], DEGRADED_NOTICE),
                             user_text=message_text(history[-1].get("content")))
        tick_soon()

        recalled = await recall(query=turn.user_text, facts=fact_store, affect=affect_store,
                                timeout=settings.recall_timeout_seconds, surface=first_turn, lore=[],
                                handoff=handoff)
        system = "\n\n".join(p for p in (_read(settings, "persona.md"), _read(settings, "core_profile.md")) if p)
        payload = {**body, "model": settings.upstream_model,
                   "messages": compose(system, history, recalled, DEGRADED_NOTICE)}
        notice = DEGRADED_NOTICE if recalled.degraded_sources else ""

        async def finish(text: str, ok: bool) -> None:
            log.complete_turn(turn.id, text)
            if not ok:
                log.set_status(turn.id, FAILED)
                if superseded:
                    life.restore(superseded)
            log.record_audit(turn_id=turn.id, device=device, created_at=clock.now(),
                             outcome="ok" if ok else "failed", upstream=settings.upstream_base_url,
                             degraded_reasons=recalled.degraded_reasons, memory_ids=recalled.memory_ids(),
                             latency_ms=int((time.monotonic() - started) * 1000))

        if body.get("stream"):
            opened = await up.open_stream(client, payload)
            if isinstance(opened, up.UpstreamError):
                await finish("", False)
                return opened
            return up.relay_stream(opened, finish, notice)

        result = await up.complete(client, payload)
        if isinstance(result, up.UpstreamError):
            await finish("", False)
            return result
        text, raw = result
        await finish(text, True)
        if notice:
            message = raw["choices"][0]["message"]
            message["content"] = (message.get("content") or "") + notice
        return raw

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Any:
        device = settings.devices.get(_bearer(request))
        if device is None:
            return _unauthorized()
        result = await run_turn(device, await request.json())
        if isinstance(result, up.UpstreamError):
            return JSONResponse(result.body, status_code=result.status_code)
        if isinstance(result, dict):
            return JSONResponse(result)
        return StreamingResponse(result, media_type="text/event-stream")

    @app.get("/v1/models")
    async def models(request: Request) -> Any:
        """Kelivo 添加供应商时会拉模型列表;对它来说,陪伴角色就是唯一的"模型"。"""
        if settings.devices.get(_bearer(request) or request.headers.get("x-api-key", "")) is None:
            return _unauthorized()
        return {"object": "list", "data": [{"id": settings.companion, "object": "model", "owned_by": "dafeiyu"}]}

    @app.post("/v1/messages")
    async def messages(request: Request) -> Any:
        device = settings.devices.get(request.headers.get("x-api-key", "") or _bearer(request))
        if device is None:
            return JSONResponse(anthropic.error(401, {"error": {"message": "unknown gateway token"}}), status_code=401)
        body = await request.json()
        model = body.get("model", settings.companion)
        result = await run_turn(device, anthropic.to_openai(body))
        if isinstance(result, up.UpstreamError):
            return JSONResponse(anthropic.error(result.status_code, result.body), status_code=result.status_code)
        if isinstance(result, dict):
            return JSONResponse(anthropic.from_openai(result, model))
        return StreamingResponse(anthropic.stream_from_openai(result, model), media_type="text/event-stream")

    def _admin(request: Request) -> bool:
        return bool(settings.admin_token) and hmac.compare_digest(_bearer(request), settings.admin_token)

    @app.post("/admin/tick")
    async def admin_tick(request: Request) -> Any:
        if not _admin(request):
            return _unauthorized()
        await tick()
        return {"ok": True}

    @app.get("/admin/turns")
    async def admin_turns(request: Request) -> Any:
        if not _admin(request):
            return _unauthorized()
        return {"turns": [{**t.__dict__, "created_at": t.created_at.isoformat()} for t in log.turns()]}

    @app.get("/admin/audit")
    async def admin_audit(request: Request) -> Any:
        if not _admin(request):
            return _unauthorized()
        return {"entries": log.audit_entries()}

    return app
