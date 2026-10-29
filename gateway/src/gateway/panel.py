"""策划数据面板(工单 09):网页上编辑人设、核心档案、网关世界书,查看版本、回滚、试匹配。

- 单独的面板密码(GATEWAY_PANEL_PASSWORD),不复用设备 Token;没设密码时面板整个不存在(404)。
- 登录后发一个签名的会话 Cookie(HttpOnly、SameSite=Strict),7 天过期;改密码后旧会话全部失效。
- 改动类请求还要带 X-Panel 请求头,挡住跨站表单提交。
- 网关只在电脑本机和 Tailscale 私网可达(ADR-0006),面板跟着网关走,不另开端口。
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import random
import time
from importlib import resources
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from gateway.curated import LORE_SETTINGS, PERSONA, PROFILE, CuratedError, CuratedStore, world_path
from gateway.compose import compose, system_head
from gateway.lorebook import ENTRY_DEFAULTS, LoreSettings, activate, entries_of, place, render

COOKIE = "dafeiyu_panel"
SESSION_SECONDS = 7 * 24 * 3600
_FAIL_DELAY_SECONDS = 1.0


def _sign(password: str, expires: int) -> str:
    key = hashlib.sha256(f"dafeiyu-panel:{password}".encode()).digest()
    mac = hmac.new(key, str(expires).encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{mac}"


def _valid_session(password: str, cookie: str | None, now: float) -> bool:
    if not cookie or "." not in cookie:
        return False
    expires, _, _mac = cookie.partition(".")
    if not expires.isdigit() or int(expires) < now:
        return False
    return hmac.compare_digest(cookie, _sign(password, int(expires)))


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def register_panel(app: FastAPI, store: CuratedStore, password: str, lore_defaults: LoreSettings) -> None:
    if not password:
        return

    def authed(request: Request) -> bool:
        return _valid_session(password, request.cookies.get(COOKIE), time.time())

    def guard(request: Request, *, mutating: bool = False) -> JSONResponse | None:
        if not authed(request):
            return _error(401, "未登录")
        if mutating and request.headers.get("x-panel") != "1":
            return _error(403, "缺少 X-Panel 请求头")
        return None

    @app.get("/panel")
    async def panel_page() -> HTMLResponse:
        html = resources.files("gateway").joinpath("panel.html").read_text(encoding="utf-8")
        return HTMLResponse(html, headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY",
                                           "Content-Security-Policy": "default-src 'self'; style-src 'self' "
                                           "'unsafe-inline'; script-src 'self' 'unsafe-inline'"})

    @app.post("/panel/api/login")
    async def login(request: Request) -> Response:
        try:
            body = await request.json()
        except ValueError:
            body = {}
        given = str(body.get("password", "")) if isinstance(body, dict) else ""
        if not hmac.compare_digest(given.encode(), password.encode()):
            await asyncio.sleep(_FAIL_DELAY_SECONDS)  # 放慢猜密码
            return _error(401, "密码不对")
        resp = JSONResponse({"ok": True})
        resp.set_cookie(COOKIE, _sign(password, int(time.time()) + SESSION_SECONDS), max_age=SESSION_SECONDS,
                        httponly=True, samesite="strict", path="/panel")
        return resp

    @app.post("/panel/api/logout")
    async def logout() -> Response:
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE, path="/panel")
        return resp

    @app.get("/panel/api/state")
    async def state(request: Request) -> Response:
        if err := guard(request):
            return err
        disabled = set(store.lore_raw().get("disabled_worlds") or [])
        worlds = []
        for name in store.world_names():
            try:
                count = len(json.loads(store.read(world_path(name)) or "{}").get("entries") or {})
                broken = False
            except ValueError:
                count, broken = 0, True
            worlds.append({"name": name, "enabled": name not in disabled, "entries": count, "broken": broken})
        settings = store.lore_settings(lore_defaults)
        return JSONResponse({"worlds": worlds, "settings": {**settings.__dict__, "disabled_worlds": sorted(disabled)},
                             "entry_defaults": ENTRY_DEFAULTS})

    def _path(kind: str, name: str = "") -> str:
        return {"persona": PERSONA, "profile": PROFILE, "settings": LORE_SETTINGS}.get(kind) or world_path(name)

    @app.get("/panel/api/doc")
    async def get_doc(request: Request, kind: str, name: str = "") -> Response:
        if err := guard(request):
            return err
        try:
            return JSONResponse({"content": store.read(_path(kind, name)) or ""})
        except CuratedError as exc:
            return _error(400, str(exc))

    @app.put("/panel/api/doc")
    async def put_doc(request: Request, kind: str, name: str = "") -> Response:
        if err := guard(request, mutating=True):
            return err
        body = await request.json()
        try:
            version = store.write(_path(kind, name), str(body.get("content", "")))
        except CuratedError as exc:
            return _error(400, str(exc))
        return JSONResponse({"ok": True, "version": version})

    @app.delete("/panel/api/doc")
    async def delete_doc(request: Request, kind: str, name: str = "") -> Response:
        if err := guard(request, mutating=True):
            return err
        if kind != "world":
            return _error(400, "只能删除世界书")
        try:
            return JSONResponse({"ok": True, "version": store.delete(world_path(name))})
        except CuratedError as exc:
            return _error(400, str(exc))

    @app.get("/panel/api/history")
    async def history(request: Request, kind: str, name: str = "") -> Response:
        if err := guard(request):
            return err
        try:
            return JSONResponse({"versions": store.history(_path(kind, name))})
        except CuratedError as exc:
            return _error(400, str(exc))

    @app.get("/panel/api/version")
    async def version(request: Request, id: int) -> Response:
        if err := guard(request):
            return err
        row = store.version(id)
        return JSONResponse(row) if row else _error(404, "没有这个版本")

    @app.post("/panel/api/rollback")
    async def rollback(request: Request, kind: str, id: int, name: str = "") -> Response:
        if err := guard(request, mutating=True):
            return err
        try:
            return JSONResponse({"ok": True, "version": store.rollback(_path(kind, name), id)})
        except CuratedError as exc:
            return _error(400, str(exc))

    @app.post("/panel/api/lore-test")
    async def lore_test(request: Request) -> Response:
        """注入预览:贴一段对话(最后一条当作本轮用户这句话),返回发给上游的完整消息排布和命中的条目。
        概率和互斥组取固定的随机数,结果可复现。召回块用占位文字代替(真实内容取决于记忆库)。"""
        if err := guard(request):
            return err
        body = await request.json()
        lines = [str(m) for m in body.get("messages", []) if str(m).strip()]
        if not lines:
            return JSONResponse({"entries": [], "layout": []})
        settings = store.lore_settings(lore_defaults)
        rng = random.Random(0)
        chosen = activate(entries_of(store.worlds()), lines, settings, rand=rng.random)
        placement = place(chosen, settings)
        # 对话按「最后一条是用户」往前交替排
        history = [{"role": "user" if (len(lines) - 1 - i) % 2 == 0 else "assistant", "content": t}
                   for i, t in enumerate(lines)]
        start = len(history) - 1
        persona = placement.fill_outlets(store.text(PERSONA))
        profile = placement.fill_outlets(store.text(PROFILE))
        head = system_head(persona, profile, placement.before, placement.after)
        messages = compose([head, ""], history, RECALL_PLACEHOLDER, start, depth=placement.depth)
        depth_text = {text: key for key, text in placement.depth.items()}
        user_at = next(i for i, m in enumerate(messages) if m.get("content") == RECALL_PLACEHOLDER) + 1
        layout: list[dict[str, Any]] = []
        injected: set[int] = set()
        for i, m in enumerate(messages):
            content = str(m.get("content") or "")
            if i == 0:
                parts = [("人设", persona), ("世界书 ↑角色定义", "\n".join(placement.before)),
                         ("核心档案", profile), ("世界书 ↓角色定义", "\n".join(placement.after))]
                kind = "系统提示开头:" + " → ".join(name for name, text in parts if text)
            elif content == RECALL_PLACEHOLDER:
                kind = "召回块"
                injected.add(i)
            elif content in depth_text and m["role"] == _ROLE[depth_text[content][1]]:
                kind = f"世界书 @D{depth_text[content][0]}"
                injected.add(i)
            elif i == user_at:
                kind = "本轮用户这句话"
            else:
                kind = "历史"
            layout.append({"role": m["role"], "kind": kind, "content": content})
        # 深度按酒馆的数法:真实的对话消息从用户这句话(0)往前数;注入的条目标它自己设定的深度,召回块是 1
        for i, item in enumerate(layout):
            if i == 0:
                item["depth"] = None
            elif i in injected:
                item["depth"] = 1 if item["kind"] == "召回块" else int(item["kind"].removeprefix("世界书 @D"))
            elif i >= user_at:
                item["depth"] = 0
            else:
                item["depth"] = sum(1 for j in range(i + 1, user_at + 1) if j not in injected)
        hits = render(chosen, settings)
        return JSONResponse({
            "entries": [{"id": h.id, "comment": e.get("comment"), "text": h.text, "constant": bool(e.get("constant")),
                         "where": _where(e)} for h, e in zip(hits, chosen, strict=True)],
            "layout": layout})


RECALL_PLACEHOLDER = "<memory>(召回块:当前时间、跨设备衔接、相关事实、心里还挂着的事、相关经历与感受。内容来自记忆库,预览里不展开)</memory>"
_ROLE = {0: "system", 1: "user", 2: "assistant"}
_POSITIONS = {0: "↑角色定义", 1: "↓角色定义", 2: "↑作者注释(@D4 系统)", 3: "↓作者注释(@D4 系统)",
              5: "↑示例(↓角色定义)", 6: "↓示例(↓角色定义)", 7: "outlet"}


def _where(e: Any) -> str:
    position = int(e.get("position") or 0)
    if position == 4:
        depth = e.get("depth")
        role = {0: "系统", 1: "用户", 2: "AI"}.get(e.get("role"), "系统")
        return f"@D{depth if depth is not None else 4} {role}"
    if position == 7:
        return f"outlet「{e.get('outletName') or ''}」"
    return _POSITIONS.get(position, "↑角色定义")
