"""真实服务契约测试(规格 Testing Decisions 的补充切入点)。

默认跳过。需要真实服务时设置环境变量后运行:
    HINDSIGHT_URL=... HINDSIGHT_API_KEY=... OMBRE_MCP_URL=... OMBRE_MCP_TOKEN=... \
    SILICONFLOW_API_KEY=... pytest tests/contract
测试会在真实记忆库里写入带 contract- 前缀的数据,请对测试用 bank / 测试用 Ombre 实例运行。
"""

from __future__ import annotations

import math
import os
import uuid

import httpx
import pytest

from gateway.adapters import HindsightStore, OmbreStore

HINDSIGHT_URL = os.environ.get("HINDSIGHT_URL", "")
OMBRE_URL = os.environ.get("OMBRE_MCP_URL", "")
SILICONFLOW_KEY = os.environ.get("SILICONFLOW_API_KEY", "")

needs_hindsight = pytest.mark.skipif(not HINDSIGHT_URL, reason="需要真实 Hindsight:设置 HINDSIGHT_URL")
needs_ombre = pytest.mark.skipif(not OMBRE_URL, reason="需要真实 Ombre-Brain:设置 OMBRE_MCP_URL")
needs_siliconflow = pytest.mark.skipif(not SILICONFLOW_KEY, reason="需要硅基流动:设置 SILICONFLOW_API_KEY")


def _hindsight() -> HindsightStore:
    return HindsightStore(HINDSIGHT_URL, os.environ.get("HINDSIGHT_BANK", "contract-test"),
                          api_key=os.environ.get("HINDSIGHT_API_KEY", ""))


def _ombre() -> OmbreStore:
    return OmbreStore(OMBRE_URL, token=os.environ.get("OMBRE_MCP_TOKEN", ""))


@needs_hindsight
async def test_hindsight_recalls_chinese_fact_through_siliconflow_reranker() -> None:
    store = _hindsight()
    await store.write_episode(f"contract-{uuid.uuid4().hex}", "用户:我养了一只叫团子的橘猫\n大肥鱼:团子听起来很可爱")

    hits = await store.recall("我的猫叫什么")

    assert any("团子" in h.text for h in hits)


@needs_hindsight
async def test_hindsight_rewrite_with_same_document_replaces_old_facts() -> None:
    store, doc = _hindsight(), f"contract-{uuid.uuid4().hex}"
    await store.write_episode(doc, "用户:我的生日是三月十四日")
    await store.write_episode(doc, "用户:我的生日是四月二十日")

    texts = " ".join(h.text for h in await store.recall("用户的生日是哪天"))

    assert "四月" in texts or "4月" in texts
    assert "三月十四" not in texts and "3月14" not in texts


@needs_ombre
async def test_ombre_search_returns_parseable_ids_after_grow() -> None:
    store = _ombre()
    marker = uuid.uuid4().hex[:8]
    await store.write_episode("contract", f"用户:今天面试{marker}没通过,有点难过\n大肥鱼:抱抱你")

    hits = await store.recall(f"面试{marker}")

    assert hits and hits[0].id and marker in hits[0].text


@needs_ombre
async def test_ombre_retried_grow_with_same_content_does_not_duplicate() -> None:
    """写入队列重试时依赖 Ombre grow 的 30 分钟同内容去重。"""
    store = _ombre()
    marker = uuid.uuid4().hex[:8]
    transcript = f"用户:今天去了{marker}公园散步\n大肥鱼:听起来很惬意"
    await store.write_episode("contract", transcript)
    await store.write_episode("contract", transcript)

    [hit] = await store.recall(f"{marker}公园")

    assert len(hit.id.split(",")) == 1


@needs_ombre
async def test_ombre_grow_succeeds_on_long_content_with_thinking_disabled() -> None:
    """长内容 grow 一直失败 = thinking 吃光了预算(Ombre CHANGELOG)。"""
    long = "\n".join(f"用户:第{i}件小事,今天天气不错\n大肥鱼:嗯嗯" for i in range(120))

    await _ombre().write_episode("contract", long)


@needs_siliconflow
async def test_free_and_pro_bge_m3_produce_the_same_vectors() -> None:
    async with httpx.AsyncClient(base_url="https://api.siliconflow.cn/v1",
                                 headers={"Authorization": f"Bearer {SILICONFLOW_KEY}"}, timeout=60) as http:
        vecs = []
        for model in ("BAAI/bge-m3", "Pro/BAAI/bge-m3"):
            resp = await http.post("/embeddings", json={"model": model, "input": "今天和大肥鱼聊了很久"})
            resp.raise_for_status()
            vecs.append(resp.json()["data"][0]["embedding"])

    a, b = vecs
    cosine = sum(x * y for x, y in zip(a, b)) / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))
    assert len(a) == len(b) == 1024
    assert cosine > 0.9999
