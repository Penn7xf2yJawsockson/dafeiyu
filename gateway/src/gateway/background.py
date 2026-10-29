"""不属于对话的请求:Kelivo 的后台任务,以及无记忆直通模型。

这类请求只原样转发给上游:不召回、不记成一轮、不进记忆,也不影响正在进行的那一轮。
"""

from __future__ import annotations

from typing import Any

from gateway.lifecycle import message_text

# Kelivo 默认提示词的开头,取自 Kelivo 源码 lib/core/providers/settings_provider.dart 的 default*Prompt。
# 用户在 Kelivo 里改过这些提示词时识别不到——那种情况请把对应的模型设成直通模型。
KELIVO_BACKGROUND_PROMPTS = (
    "I will give you some dialogue content in the `<content>` block.",          # 生成标题
    "Suggest up to 3 useful next messages for the user",                        # 聊天建议
    "I will give you user messages from a conversation in the `<messages>` block.",  # 总结
    "Summarize the following conversation history so the conversation can continue",  # 压缩上下文
    "You are an OCR assistant.",                                                # OCR
    "You are a translation expert, skilled in translating various languages",   # 翻译
)


KELIVO_COMPRESS_PROMPT = "Summarize the following conversation history so the conversation can continue"


def _candidates(body: dict[str, Any]) -> list[str]:
    messages = body.get("messages") or []
    picked = [m for m in messages if m.get("role") == "system"]
    users = [m for m in messages if m.get("role") == "user"]
    if users:
        picked.append(users[-1])
    return [message_text(m.get("content")).lstrip() for m in picked]


def is_compress(body: dict[str, Any]) -> bool:
    """Kelivo 的「压缩上下文」请求(默认提示词)。网关借此在压缩前把这一段记忆写完。"""
    return any(text.startswith(KELIVO_COMPRESS_PROMPT) for text in _candidates(body))


def is_background(body: dict[str, Any]) -> bool:
    """系统消息或最后一条用户消息以 Kelivo 后台任务的默认提示词开头。"""
    return any(text.startswith(KELIVO_BACKGROUND_PROMPTS) for text in _candidates(body))
