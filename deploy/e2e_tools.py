"""工具调用端到端自测:走真实中转,让模型调用一个假的天气工具,再把结果交回去拿最终回复。

在独立的自测网关(compose.e2e.yml,端口 18081)上运行,不碰真实记忆。不打印任何密钥。
"""

from __future__ import annotations

import json
import pathlib
import sys

import httpx

BASE = "http://127.0.0.1:18081"
env = dict(line.split("=", 1) for line in pathlib.Path(".env").read_text(encoding="utf-8").splitlines()
           if "=" in line and not line.startswith("#"))
PC = dict(pair.split("=", 1) for pair in env["GATEWAY_DEVICES"].split(","))["pc"]
ADMIN = env["GATEWAY_ADMIN_TOKEN"]
TOOLS = [{"type": "function", "function": {
    "name": "get_weather", "description": "查询某个城市明天的天气",
    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}]
http = httpx.Client(base_url=BASE, timeout=180, headers={"Authorization": f"Bearer {PC}"})


def stream(messages: list[dict[str, object]]) -> tuple[str, list[dict[str, str]], str]:
    text, calls, finish = [], {}, ""
    with http.stream("POST", "/v1/chat/completions",
                     json={"model": "dafeiyu", "messages": messages, "tools": TOOLS, "stream": True}) as r:
        if r.status_code != 200:
            sys.exit(f"HTTP {r.status_code}: {r.read().decode()[:400]}")
        for line in r.iter_lines():
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            data = json.loads(line[6:])
            if "error" in data:
                sys.exit(f"流式出错:{data['error']}")
            for c in data.get("choices", []):
                d = c.get("delta") or {}
                text.append(d.get("content") or "")
                for t in d.get("tool_calls") or []:
                    slot = calls.setdefault(t["index"], {"id": "", "name": "", "arguments": ""})
                    slot["id"] = t.get("id") or slot["id"]
                    fn = t.get("function") or {}
                    slot["name"] = fn.get("name") or slot["name"]
                    slot["arguments"] += fn.get("arguments") or ""
                finish = c.get("finish_reason") or finish
    return "".join(text), [calls[i] for i in sorted(calls)], finish


ask = {"role": "user", "content": "帮我查一下杭州明天的天气,要不要带伞?"}
text1, calls, finish1 = stream([ask])
print(f"① 第一次请求:finish={finish1} 工具调用={[(c['name'], c['arguments']) for c in calls]}")
if not calls:
    sys.exit("❌ 模型没有调用工具(中转或模型可能不支持 tools)")

follow: list[dict[str, object]] = [ask, {"role": "assistant", "content": text1 or None, "tool_calls": [
    {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}} for c in calls]}]
follow += [{"role": "tool", "tool_call_id": c["id"], "content": "杭州 明天 中雨 14-18℃ 降水概率 85%"} for c in calls]
text2, calls2, finish2 = stream(follow)
print(f"② 回传工具结果后:finish={finish2}\n   最终回复:{text2[:120]}")

admin = {"Authorization": f"Bearer {ADMIN}"}
turns = http.get("/admin/turns", headers=admin).json()["turns"]
audit = http.get("/admin/audit", headers=admin).json()["entries"]
print("原文日志里的轮:", [(t["user_text"][:12], t["assistant_text"][:20], t["status"]) for t in turns])
print("审计:", [(e["outcome"], e["tool_calls"], e["degraded_reasons"] or "无降级") for e in audit])
ok = (len(turns) == 1 and turns[0]["assistant_text"] == text2 and "85%" not in turns[0]["assistant_text"].replace(text2, "")
      and finish2 == "stop" and any("伞" in text2 or "雨" in text2 for _ in [0]))
print("\n结果:", "✅ 工具调用走通,一轮里只记了提问和最终回复" if ok else "❌ 有问题,见上面")
sys.exit(0 if ok else 1)
