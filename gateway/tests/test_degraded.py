"""工单 07:记忆库故障或召回超时 → 降级轮:照常回答、可见标记、审计记录、核心档案照常注入。"""

import json

from conftest import Harness

from gateway.stores import MemoryHit

NOTICE = "⚠ 记忆不完整"


def test_slow_store_degrades_the_turn_but_reply_still_arrives(harness: Harness) -> None:
    harness.facts.hang_seconds = 1.0  # 超过 0.2 秒的召回超时
    harness.affect.hits = [MemoryHit("o1", "还能用的情感记忆")]
    harness.upstream.reply = "我在呢"

    resp = harness.chat([{"role": "user", "content": "在吗"}])

    assert resp.status_code == 200
    content = resp.json()["choices"][0]["message"]["content"]
    assert content.startswith("我在呢") and NOTICE in content
    [entry] = harness.audit()
    assert entry["degraded_sources"] == ["facts"]
    assert entry["memory_ids"] == ["affect:o1"]
    sent = harness.upstream.last["messages"]
    assert "用户生日:3月14日。" in sent[0]["content"]  # 核心档案在降级轮照常注入
    assert "还能用的情感记忆" in sent[-2]["content"]


def test_streaming_degraded_turn_shows_notice_before_finish(harness: Harness) -> None:
    harness.affect.fail = True
    harness.upstream.reply = "嗯嗯"

    resp = harness.chat([{"role": "user", "content": "聊聊"}], stream=True)

    chunks = [json.loads(line[6:]) for line in resp.text.splitlines()
              if line.startswith("data: ") and line != "data: [DONE]"]
    texts = [c["choices"][0]["delta"].get("content") or "" for c in chunks]
    finish_index = next(i for i, c in enumerate(chunks) if c["choices"][0]["finish_reason"])
    assert any(NOTICE in t for t in texts[:finish_index])
    assert "".join(texts).startswith("嗯嗯")


def test_notice_is_stripped_from_multipart_assistant_content(harness: Harness) -> None:
    harness.chat([
        {"role": "user", "content": "看图"},
        {"role": "assistant", "content": [{"type": "text", "text": "好看\n\n" + NOTICE}]},
        {"role": "user", "content": "再说说"},
    ])

    assert NOTICE not in json.dumps(harness.upstream.last["messages"], ensure_ascii=False)


def test_notice_is_not_part_of_memory_or_upstream_history(harness: Harness) -> None:
    harness.affect.fail = True
    harness.upstream.reply = "好呀"
    shown = harness.chat([{"role": "user", "content": "一"}]).json()["choices"][0]["message"]["content"]
    harness.affect.fail = False

    harness.chat([{"role": "user", "content": "一"}, {"role": "assistant", "content": shown},
                  {"role": "user", "content": "二"}])
    harness.tick()

    first, _second = harness.turns()
    assert first["status"] == "settled"  # 带标记的回复也能被识别为同一轮
    assert first["assistant_text"] == "好呀"
    assert all(NOTICE not in str(m["content"]) for m in harness.upstream.last["messages"])
