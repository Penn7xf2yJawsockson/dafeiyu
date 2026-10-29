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
from gateway.lorebook import LoreSettings, activate, entries_of, render

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
        return JSONResponse({"worlds": worlds, "settings": {**settings.__dict__, "disabled_worlds": sorted(disabled)}})

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
        """试匹配:贴一段对话,看哪些条目会被注入。概率和互斥组取固定的随机数,结果可复现。"""
        if err := guard(request):
            return err
        body = await request.json()
        recent = [str(m) for m in body.get("messages", []) if str(m).strip()]
        settings = store.lore_settings(lore_defaults)
        rng = random.Random(0)
        chosen = activate(entries_of(store.worlds()), recent, settings, rand=rng.random)
        hits = render(chosen, settings)
        return JSONResponse({"entries": [{"id": h.id, "comment": e.get("comment"), "text": h.text,
                                          "constant": bool(e.get("constant"))} for h, e in zip(hits, chosen, strict=True)]})
