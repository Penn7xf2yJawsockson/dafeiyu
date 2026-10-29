"""不属于对话的请求:Kelivo / DSH 的后台任务,以及无记忆直通模型;还有 DSH 注入的宿主上下文消息。

这类请求只原样转发给上游:不召回、不记成一轮、不进记忆,也不影响正在进行的那一轮。
"""

from __future__ import annotations

import re
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

# DSH 2.0.18 随包插件的提示词开头:起会话标题(dsh-session-title-*-llm)、压缩(dsh-compaction-basic)。
DSH_COMPRESS_PROMPT = "You are now acting as a compaction engine for this AI coding assistant."
DSH_BACKGROUND_PROMPTS = (
    "Create a concise title for an AI coding-assistant session",                # 起标题(系统消息)
    "Generate the session title from this JSON array of human messages:",       # 起标题(用户消息)
    "Update the session title from this JSON object:",                          # 更新标题
    DSH_COMPRESS_PROMPT,                                                        # 压缩
)
COMPRESS_PROMPTS = (KELIVO_COMPRESS_PROMPT, DSH_COMPRESS_PROMPT)
BACKGROUND_PROMPTS = KELIVO_BACKGROUND_PROMPTS + DSH_BACKGROUND_PROMPTS

# DSH 以用户消息身份注入的宿主上下文(都排在用户那句话之后):
#   dsh-time-context      "Time sampled while preparing turn N, step M: …"
#   dsh-system-prompt     "Current runtime context. This snapshot supersedes …"(工作目录、环境)
#   dsh-agent-loop        "Current runtime context: none. …"
#   dsh-tool-skill 等     "<system-reminder>…"(技能清单、工作区指令等)
_CLIENT_CONTEXT = re.compile(r"Time sampled while preparing turn \d+, step \d+: |Current runtime context[.:]|<system-reminder>")


def is_client_context(message: dict[str, Any]) -> bool:
    """客户端注入的上下文,不是用户说的话:不算本轮的用户消息、不召回、不进记忆,但仍发给上游。"""
    return message.get("role") == "user" and bool(_CLIENT_CONTEXT.match(message_text(message.get("content")).lstrip()))


def _candidates(body: dict[str, Any]) -> list[str]:
    messages = body.get("messages") or []
    picked = [m for m in messages if m.get("role") == "system"]
    users = [m for m in messages if m.get("role") == "user" and not is_client_context(m)]
    if users:
        picked.append(users[-1])
    return [message_text(m.get("content")).lstrip() for m in picked]


def is_compress(body: dict[str, Any]) -> bool:
    """「压缩上下文」请求(Kelivo / DSH 默认提示词)。网关借此在压缩前把这一段记忆写完。"""
    return any(text.startswith(COMPRESS_PROMPTS) for text in _candidates(body))


def is_background(body: dict[str, Any]) -> bool:
    """系统消息或最后一条用户消息以 Kelivo / DSH 后台任务的默认提示词开头。"""
    return any(text.startswith(BACKGROUND_PROMPTS) for text in _candidates(body))
