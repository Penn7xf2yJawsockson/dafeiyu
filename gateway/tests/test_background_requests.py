"""Kelivo 的后台请求(生成标题、聊天建议、总结、压缩上下文、OCR、翻译)和无记忆直通模型。

这些请求不是用户在说话:不召回、不记成一轮、不进记忆,只原样转发给上游。
"""

import pytest
from conftest import Harness

# Kelivo 默认提示词的开头(取自 Kelivo 源码 settings_provider.dart 的 default*Prompt)
KELIVO_DEFAULT_PROMPTS = {
    "标题": "I will give you some dialogue content in the `<content>` block.\nYou need to summarize the conversation "
            "between user and assistant into a short title.",
    "建议": "Suggest up to 3 useful next messages for the user, based on the conversation below.",
    "总结": "I will give you user messages from a conversation in the `<messages>` block.\nGenerate or update a brief "
            "summary of the user's questions and intentions",
    "压缩": "Summarize the following conversation history so the conversation can continue using the summary.",
    "OCR": "You are an OCR assistant.\n\nExtract all visible text from the image",
    "翻译": "You are a translation expert, skilled in translating various languages",
}


def test_model_list_offers_a_direct_model_without_memory(harness: Harness) -> None:
    resp = harness.client.get("/v1/models", headers={"Authorization": "Bearer tok-pc"})

    assert [m["id"] for m in resp.json()["data"]] == ["dafeiyu", "dafeiyu-direct"]


def test_direct_model_is_a_plain_passthrough(harness: Harness) -> None:
    sent = [{"role": "system", "content": "酒馆角色卡"}, {"role": "user", "content": "你好"}]

    resp = harness.chat(sent, model="dafeiyu-direct")

    assert resp.status_code == 200
    assert harness.upstream.last["messages"] == sent
    assert harness.upstream.last["model"] == "deepseek-flash"
    assert harness.turns() == [] and harness.facts.queries == []


@pytest.mark.parametrize("kind", KELIVO_DEFAULT_PROMPTS)
def test_kelivo_background_requests_never_become_turns(harness: Harness, kind: str) -> None:
    harness.chat([{"role": "user", "content": "我叫小鱼"}])
    prompt = KELIVO_DEFAULT_PROMPTS[kind] + "\n<content>\n用户:我叫小鱼\n大肥鱼:好的\n</content>"

    harness.chat([{"role": "user", "content": prompt}])

    assert harness.upstream.last["messages"] == [{"role": "user", "content": prompt}]
    [turn] = harness.turns()
    assert turn["user_text"] == "我叫小鱼"
    assert harness.facts.queries == ["我叫小鱼"]
    # 后台请求不影响正在进行的那一轮;只有压缩上下文例外:它会先确定并写完这一段(test_compress_sync)
    assert turn["status"] == ("settled" if kind == "压缩" else "pending")


def test_ordinary_message_mentioning_summaries_is_still_a_turn(harness: Harness) -> None:
    harness.chat([{"role": "user", "content": "帮我 summarize the following conversation history 好吗"}])

    assert len(harness.turns()) == 1
