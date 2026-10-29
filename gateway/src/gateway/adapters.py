"""真实记忆库适配器。只能用契约测试(tests/contract,需真实服务)验证。

- 事实库:Hindsight 1.1 REST(接口取自其 OpenAPI 1.1.0)
- 情感库:Ombre-Brain MCP(streamable-http,无状态 JSON 响应,2.8.5 起)
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from gateway.stores import MemoryHit


class HindsightStore:
    def __init__(self, base_url: str, bank_id: str, api_key: str = "", recall_max_tokens: int = 1500,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._http = httpx.AsyncClient(base_url=base_url, headers=headers, timeout=180, transport=transport)
        self._path = f"/v1/default/banks/{bank_id}/memories"
        self._max_tokens = recall_max_tokens

    async def recall(self, query: str) -> list[MemoryHit]:
        resp = await self._http.post(f"{self._path}/recall", json={"query": query, "max_tokens": self._max_tokens})
        if _bank_not_found(resp):
            return []  # 全新部署时 bank 要等第一次 retain 才建出来,这时就是"还没有记忆",不是故障
        resp.raise_for_status()
        return [MemoryHit(r["id"], r["text"]) for r in resp.json()["results"]]

    async def surface(self) -> list[MemoryHit]:
        return []  # 事实库没有"主动浮现";关键事实走核心档案

    async def write_episode(self, episode_id: str, transcript: str) -> None:
        # 同一 document_id 再次 retain 会删除旧版本并整体重新处理(Hindsight retain 文档),
        # 所以每次都发整段已确定的原文,写入天然幂等。同步写入,返回即表示已入库。
        item = {"content": transcript, "document_id": episode_id, "context": "与陪伴角色大肥鱼的日常对话"}
        resp = await self._http.post(self._path, json={"items": [item], "async": False})
        resp.raise_for_status()


def _bank_not_found(resp: httpx.Response) -> bool:
    """Hindsight 1.1:不存在的 bank 返回 404 {"detail": "Bank '<id>' not found"}。
    只认这一种 404,其他 404(路径写错等)仍按错误处理,免得把配置错误静默成"没有记忆"。"""
    if resp.status_code != 404:
        return False
    try:
        detail = resp.json().get("detail", "")
    except ValueError:
        return False
    return isinstance(detail, str) and detail.startswith("Bank '") and detail.endswith("' not found")


_IDS_BLOCK = re.compile(r"=== ombre:result-ids ===\s*```json\s*(\{.*?\})\s*```", re.S)


def parse_ombre_result(text: str) -> list[MemoryHit]:
    """with_ids=True 的返回:人类可读正文 + 末尾的机器可读 ID 块(Ombre 3.6.4 契约)。

    正文无法可靠地按桶切开,所以整段正文作为一条记忆返回,ID 用逗号连接进审计。
    """
    match = _IDS_BLOCK.search(text)
    if match is None:
        return []
    ids: list[str] = json.loads(match.group(1)).get("bucket_ids", [])
    body = text[: match.start()].strip()
    return [MemoryHit(",".join(ids), body)] if ids and body else []


class OmbreStore:
    def __init__(self, mcp_url: str, token: str = "", search_max_results: int = 5, surface_max_tokens: int = 2000,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        headers = {"Accept": "application/json, text/event-stream"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._http = httpx.AsyncClient(headers=headers, timeout=300, transport=transport)
        self._url = mcp_url
        self._max_results = search_max_results
        self._surface_tokens = surface_max_tokens
        self._seq = 0

    async def _call(self, tool: str, arguments: dict[str, Any]) -> str:
        self._seq += 1
        payload = {"jsonrpc": "2.0", "id": self._seq, "method": "tools/call",
                   "params": {"name": tool, "arguments": arguments}}
        resp = await self._http.post(self._url, json=payload)
        resp.raise_for_status()
        body = resp.json()
        if "error" in body:
            raise RuntimeError(f"ombre {tool}: {body['error']}")
        result = body["result"]
        if result.get("isError"):
            raise RuntimeError(f"ombre {tool}: {result}")
        return "".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")

    async def recall(self, query: str) -> list[MemoryHit]:
        text = await self._call("breath_search", {"query": query, "max_results": self._max_results,
                                                  "mode": "automatic", "with_ids": True})
        return parse_ombre_result(text)

    async def surface(self) -> list[MemoryHit]:
        text = await self._call("breath_advanced", {"max_tokens": self._surface_tokens, "with_ids": True})
        return parse_ombre_result(text)

    async def write_episode(self, episode_id: str, transcript: str) -> None:
        await self._call("grow", {"content": transcript})
