"""三项改动的端到端自测(真实中转,独立自测网关 18081,不碰真实记忆):
1. Kelivo 的系统消息保留在人设之后;2. 召回块里有当前本地时间;3. 标题生成请求与直通模型不记成一轮。
"""

from __future__ import annotations

import json
import pathlib
import sys

import httpx

env = dict(line.split("=", 1) for line in pathlib.Path(".env").read_text(encoding="utf-8").splitlines()
           if "=" in line and not line.startswith("#"))
PC = dict(p.split("=", 1) for p in env["GATEWAY_DEVICES"].split(","))["pc"]
http = httpx.Client(base_url="http://127.0.0.1:18081", timeout=180, headers={"Authorization": f"Bearer {PC}"})
admin = {"Authorization": f"Bearer {env['GATEWAY_ADMIN_TOKEN']}"}
debug = pathlib.Path("data/gateway-e2e/debug")


def last_capture() -> dict[str, object]:
    capture: dict[str, object] = json.loads(sorted(debug.glob("*.json"))[-1].read_text(encoding="utf-8"))
    return capture


skills = "<available_skills>\n- visualize — 交互式图表(/skills/visualize/SKILL.md)\n</available_skills>"
r = http.post("/v1/chat/completions", json={"model": "dafeiyu", "messages": [
    {"role": "system", "content": skills}, {"role": "user", "content": "今天几号?星期几?只回答日期和星期"}]})
reply = r.json()["choices"][0]["message"]["content"]
out = last_capture()["outgoing"]["messages"]  # type: ignore[index]
print("① 普通对话 回复:", reply.strip()[:60])
print("   发给上游:", [(m["role"], m["content"][:28].replace("\n", "⏎")) for m in out])  # type: ignore[index]

title = ("I will give you some dialogue content in the `<content>` block.\nYou need to summarize the conversation "
         "between user and assistant into a short title.\n<content>\n用户:今天几号\n大肥鱼:" + reply + "\n</content>")
t = http.post("/v1/chat/completions", json={"model": "dafeiyu", "messages": [{"role": "user", "content": title}]})
print("② 标题请求 回复:", t.json()["choices"][0]["message"]["content"].strip()[:30])
d = http.post("/v1/chat/completions", json={"model": "dafeiyu-direct", "messages": [{"role": "user", "content": "回复OK"}]})
print("③ 直通模型 回复:", d.json()["choices"][0]["message"]["content"].strip()[:30])

turns = http.get("/admin/turns", headers=admin).json()["turns"]
print("原文日志里的轮:", [t_["user_text"][:20] for t_ in turns])
checks = {
    "Kelivo 系统消息在人设之后": [m["role"] for m in out[:2]] == ["system", "system"] and out[1]["content"] == skills,  # type: ignore[index]
    "召回块里有当前时间": "现在是 " in out[-2]["content"],  # type: ignore[index]
    "只记了普通对话这一轮": len(turns) == 1 and turns[0]["user_text"].startswith("今天几号"),
}
for k, v in checks.items():
    print(("✅ " if v else "❌ ") + k)
sys.exit(0 if all(checks.values()) else 1)
