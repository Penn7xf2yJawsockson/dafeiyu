"""网关 HTTP 入口。"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import random
import time
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any, Protocol

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from gateway import anthropic
from gateway import upstream as up
from gateway.background import is_background, is_compress, is_client_context
from gateway.compose import compose, local_time_line, render_recall_block, system_head, turn_start
from gateway.config import Settings
from gateway.curated import PERSONA, PROFILE, CuratedStore
from gateway.lifecycle import Lifecycle, context_hash, message_text
from gateway.lorebook import LoreSettings, Placement, activate, entries_of, place
from gateway.panel import register_panel
from gateway.recall import recall
from gateway.stores import MemoryStore
from gateway.transcript import FAILED, TranscriptLog

DEGRADED_NOTICE = "\n\n⚠ 记忆不完整"
logger = logging.getLogger("gateway")

TurnResult = up.UpstreamError | dict[str, Any] | AsyncIterator[bytes]
"""失败 / 非流式的完整回复 / 流式的 OpenAI SSE 字节流。"""


class Clock(Protocol):
    def now(self) -> datetime: ...


SEND_MESSAGE_TOOL = "send_message"  # dsh-segmented-reply 插件注册的工具名


def sent_messages(turn_messages: list[dict[str, Any]]) -> list[str]:
    """本轮 send_message 调用发出的消息,按调用顺序。参数写坏的那次跳过。"""
    out: list[str] = []
    for m in turn_messages:
        for call in m.get("tool_calls") or [] if m.get("role") == "assistant" else []:
            fn = call.get("function") or {}
            if fn.get("name") != SEND_MESSAGE_TOOL:
                continue
            try:
                content = json.loads(fn.get("arguments") or "{}").get("content")
            except (ValueError, AttributeError):
                continue
            if isinstance(content, str) and content.strip():
                out.append(content.strip())
    return out


def _bearer(request: Request) -> str:
    return request.headers.get("authorization", "").removeprefix("Bearer ").strip()


_SAFE_HEADERS = ("user-agent", "content-type", "accept", "anthropic-version", "x-stainless-lang", "x-stainless-package-version")


def _incoming(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """抓包用:只留无害的请求头,Authorization / x-api-key 一律不存。"""
    headers = {k: v for k, v in request.headers.items() if k.lower() in _SAFE_HEADERS}
    return {"path": request.url.path, "headers": headers, "body": body}


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
    curated = CuratedStore(settings.data_dir, clock)
    lore_defaults = LoreSettings()

    def lore_for(history: list[dict[str, Any]], start: int, turn_id: str) -> Placement:
        """网关世界书:扫描本轮用户这句话及之前的消息(不含工具回合与客户端注入的上下文),按条目的注入位置分配。
        确定性注入,不经过召回超时,降级轮照样有(工单 09)。概率与互斥组用这一轮的 ID 做种子:同一轮的
        工具回合重算结果相同,缓存前缀不变。读不了世界书时这一轮不注入,不影响回复。"""
        try:
            recent = [message_text(m.get("content")) for m in history[: start + 1]
                      if m.get("role") in ("user", "assistant")]
            settings_ = curated.lore_settings(lore_defaults)
            rng = random.Random(turn_id)
            return place(activate(entries_of(curated.worlds()), recent, settings_, rand=rng.random), settings_)
        except Exception:
            logger.exception("网关世界书匹配失败,本轮不注入")
            return Placement()
    life = Lifecycle(settings, log, fact_store, affect_store, notice=DEGRADED_NOTICE)
    drain_lock = asyncio.Lock()
    direct_model = f"{settings.companion}-direct"  # 无记忆直通:给标题生成、酒馆、Agent 等用
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
            await life.drain(clock.now())

    def tick_soon() -> None:
        """立刻在后台推进写入队列:刚确定的轮尽早写进事实库,下一轮就能查到。"""
        if settings.background_worker:
            task = asyncio.create_task(tick())
            background.add(task)
            task.add_done_callback(background.discard)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        task: asyncio.Task[None] | None = None
        if found := life.reconcile():
            logger.warning("启动对账:补入 %d 个漏掉的记忆写入任务", found)
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
    register_panel(app, curated, settings.panel_password, lore_defaults)

    async def run_turn(device: str, body: dict[str, Any], incoming: dict[str, Any] | None = None) -> TurnResult:
        """一轮的完整处理。body 是 OpenAI Chat Completions 格式。

        带工具的一轮会跨好几次请求(模型要调用工具 → Kelivo 回传工具结果 → …)。以工具结果结尾的请求
        属于同一轮的「工具回合」:不新开一轮、不重新召回,原样复用这一轮的召回块;
        只有用户说的话和最终回复写进原文日志里的这一轮,工具调用与工具结果不进记忆。

        从 resolve_previous 到 open_turn 之间没有 await,在单线程事件循环里天然不会与
        其他请求或后台扫描交错;改动这一段时不要在中间插入 await。
        """
        if is_compress(body):
            # 压缩上下文:模型马上要看不到前面的原文,先把这一段写进两个记忆库(后台进行,不拖慢压缩)
            life.close_current_episode(device, settings.companion, clock.now())
            tick_soon()
        if body.get("model") == direct_model or is_background(body):
            return await passthrough(body, incoming)
        started = time.monotonic()
        # DSH 注入的宿主上下文(时间、运行环境、技能清单)不是用户说的话:判定轮次、召回、记忆都看去掉它之后的历史;发给上游时原样保留
        full = [m for m in body.get("messages") or [] if m.get("role") != "system"]
        history = [m for m in full if not is_client_context(m)]
        client_system = "\n\n".join(text for m in body.get("messages") or []
                                     if m.get("role") == "system" and (text := message_text(m.get("content"))))
        start = turn_start(history)
        if start is None:
            return up.UpstreamError(400, {"error": {"message": "messages must contain a user message"}})
        companion, now = settings.companion, clock.now()

        tool_round = history[-1].get("role") == "tool"
        turn = life.tool_round_of(device, companion, history, start) if tool_round else None
        superseded: str | None = None
        if turn is not None:
            block, reasons, memory_ids = log.saved_recall(turn.id)
            lore = lore_for(history, start, turn.id)
        else:
            tool_round = False  # 找不到所属的一轮(比如中途重启):当作新的一轮,从那条用户消息开始
            superseded = life.resolve_previous(device, companion, history[: start + 1])
            episode_id, first_turn = life.episode_for(device, companion, now)
            handoff = life.handoff(device, companion, now) if first_turn else []
            turn = log.open_turn(device=device, companion=companion, episode_id=episode_id, created_at=now,
                                 context_hash=context_hash(history[:start], DEGRADED_NOTICE),
                                 user_text=message_text(history[start].get("content")))
            tick_soon()
            recalled = await recall(query=turn.user_text, facts=fact_store, affect=affect_store,
                                    timeout=settings.recall_timeout_seconds, surface=first_turn,
                                    handoff=handoff)
            block = render_recall_block(recalled, local_time_line(now, settings.timezone))
            lore = lore_for(history, start, turn.id)
            reasons = recalled.degraded_reasons
            memory_ids = recalled.memory_ids() + [f"lore:{i}" for i in lore.ids]
            log.save_recall(turn.id, block, reasons, memory_ids)

        head = system_head(lore.fill_outlets(curated.text(PERSONA)), lore.fill_outlets(curated.text(PROFILE)),
                           lore.before, lore.after)
        segments = [head, client_system]
        payload = {**body, "model": settings.upstream_model,
                   "messages": compose(segments, full, block, next(i for i, m in enumerate(full) if m is history[start]),
                                       DEGRADED_NOTICE, depth=lore.depth)}
        notice = DEGRADED_NOTICE if reasons else ""
        capture(incoming or {"body": body}, payload)

        async def finish(text: str, ok: bool, tools: list[str]) -> None:
            if ok and not tools:
                # DSH 分段回复插件:她说的话在本轮 send_message 的参数里,按顺序拼在最终回复前面
                log.complete_turn(turn.id, "\n".join(p for p in (*sent_messages(history[start:]), text) if p))
            elif not ok and not tool_round:
                log.complete_turn(turn.id, "")
                log.set_status(turn.id, FAILED)
                if superseded:
                    life.restore(superseded)
            # 工具回合失败时这一轮保持待定:Kelivo 重试同一个工具回合还能接上
            log.record_audit(turn_id=turn.id, device=device, created_at=clock.now(),
                             outcome="ok" if ok else "failed", upstream=settings.upstream_base_url,
                             degraded_reasons=reasons, memory_ids=memory_ids, tool_calls=tools,
                             latency_ms=int((time.monotonic() - started) * 1000))

        if body.get("stream"):
            opened = await up.open_stream(client, payload)
            if isinstance(opened, up.UpstreamError):
                await finish("", False, [])
                return opened
            return up.relay_stream(opened, finish, notice)

        result = await up.complete(client, payload)
        if isinstance(result, up.UpstreamError):
            await finish("", False, [])
            return result
        text, raw, tools = result
        await finish(text, True, tools)
        if notice and not tools:
            message = raw["choices"][0]["message"]
            message["content"] = (message.get("content") or "") + notice
        return raw

    async def passthrough(body: dict[str, Any], incoming: dict[str, Any] | None) -> TurnResult:
        """无记忆直通:Kelivo 的后台任务,或客户端选了直通模型。原样转发,不召回、不记录、不进记忆。"""
        payload = {**body, "model": settings.upstream_model}
        capture(incoming or {"body": body}, payload)
        if body.get("stream"):
            opened = await up.open_stream(client, payload)
            if isinstance(opened, up.UpstreamError):
                return opened

            async def ignore(_text: str, _ok: bool, _tools: list[str]) -> None:
                return None
            return up.relay_stream(opened, ignore)
        result = await up.complete(client, payload)
        return result if isinstance(result, up.UpstreamError) else result[1]

    def capture(incoming: dict[str, Any], outgoing: dict[str, Any]) -> None:
        """抓包开关(GATEWAY_DEBUG_CAPTURE=N):保留最近 N 次的原始请求与发给上游的请求,用于排查。
        只写本机 data/ 下(不进 git);不含任何 Token 或密钥。"""
        if settings.debug_capture <= 0:
            return
        folder = settings.data_dir / "debug"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{time.time_ns()}.json"
        path.write_text(json.dumps({"at": clock.now().isoformat(), "incoming": incoming, "outgoing": outgoing},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
        for old in sorted(folder.glob("*.json"))[: -settings.debug_capture]:
            old.unlink(missing_ok=True)

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Any:
        device = settings.devices.get(_bearer(request))
        if device is None:
            return _unauthorized()
        body = await request.json()
        result = await run_turn(device, body, _incoming(request, body))
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
        return {"object": "list", "data": [{"id": m, "object": "model", "owned_by": "dafeiyu"}
                                           for m in (settings.companion, direct_model)]}

    @app.post("/v1/messages")
    async def messages(request: Request) -> Any:
        device = settings.devices.get(request.headers.get("x-api-key", "") or _bearer(request))
        if device is None:
            return JSONResponse(anthropic.error(401, {"error": {"message": "unknown gateway token"}}), status_code=401)
        body = await request.json()
        model = body.get("model", settings.companion)
        result = await run_turn(device, anthropic.to_openai(body), _incoming(request, body))
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
