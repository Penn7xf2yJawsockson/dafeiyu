"""生产入口:从环境变量组装网关。密钥只来自环境变量(由 .env 注入),不进仓库。"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from gateway.adapters import HindsightStore, OmbreStore
from gateway.app import create_app
from gateway.config import Settings


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or value == "":
        raise SystemExit(f"缺少环境变量 {name}")
    return value


def _devices(spec: str) -> dict[str, str]:
    """GATEWAY_DEVICES="pc=TOKEN_A,phone=TOKEN_B" → {TOKEN_A: "pc", TOKEN_B: "phone"}"""
    out: dict[str, str] = {}
    for pair in spec.split(","):
        name, _, token = pair.strip().partition("=")
        if not name or not token:
            raise SystemExit(f"GATEWAY_DEVICES 格式错误:{pair!r}")
        out[token] = name
    return out


def build() -> FastAPI:
    settings = Settings(
        data_dir=Path(_env("GATEWAY_DATA_DIR", "/data")),
        devices=_devices(_env("GATEWAY_DEVICES")),
        upstream_base_url=_env("UPSTREAM_BASE_URL", "https://api.deepseek.com"),
        upstream_api_key=_env("UPSTREAM_API_KEY"),
        upstream_model=_env("UPSTREAM_MODEL", "deepseek-flash"),
        admin_token=_env("GATEWAY_ADMIN_TOKEN"),
        debug_capture=int(os.environ.get("GATEWAY_DEBUG_CAPTURE", "0") or "0"),
    )
    clock = SystemClock()
    facts = HindsightStore(_env("HINDSIGHT_URL", "http://hindsight:8888"), _env("HINDSIGHT_BANK", "dafeiyu"),
                           api_key=_env("HINDSIGHT_API_KEY"), clock=clock, tz=settings.timezone)
    affect = OmbreStore(_env("OMBRE_MCP_URL", "http://ombre:8000/mcp"), token=_env("OMBRE_MCP_TOKEN"))
    return create_app(settings, fact_store=facts, affect_store=affect, clock=clock)


def run() -> None:
    uvicorn.run(build(), host="0.0.0.0", port=int(os.environ.get("GATEWAY_PORT", "8080")))


if __name__ == "__main__":
    run()
