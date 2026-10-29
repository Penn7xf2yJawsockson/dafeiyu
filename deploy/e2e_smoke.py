"""端到端自测:电脑上说一件事 → 确定并写入事实库 → 换手机问,看能不能想起来。

读取 .env 里的设备 Token 和管理 Token,不打印任何密钥。
"""

from __future__ import annotations

import json
import pathlib
import sys

import httpx

BASE = "http://127.0.0.1:18081"
env = dict(line.split("=", 1) for line in pathlib.Path(".env").read_text(encoding="utf-8").splitlines()
           if "=" in line and not line.startswith("#"))
devices = dict(pair.split("=", 1) for pair in env["GATEWAY_DEVICES"].split(","))
PC, PHONE, ADMIN = devices["pc"], devices["phone"], env["GATEWAY_ADMIN_TOKEN"]
http = httpx.Client(base_url=BASE, timeout=180)


def chat(token: str, messages: list[dict[str, str]]) -> str:
    text = []
    with http.stream("POST", "/v1/chat/completions", headers={"Authorization": f"Bearer {token}"},
                     json={"model": "dafeiyu", "messages": messages, "stream": True}) as resp:
        if resp.status_code != 200:
            sys.exit(f"HTTP {resp.status_code}: {resp.read().decode()[:300]}")
        for line in resp.iter_lines():
            if line.startswith("data: ") and line != "data: [DONE]":
                data = json.loads(line[6:])
                if "error" in data:
                    sys.exit(f"流式出错:{data['error']}")
                text += [(c.get("delta") or {}).get("content") or "" for c in data.get("choices", [])]
    return "".join(text)


def admin(method: str, path: str) -> dict[str, object]:
    resp = http.request(method, path, headers={"Authorization": f"Bearer {ADMIN}"})
    resp.raise_for_status()
    result: dict[str, object] = resp.json()
    return result


u1 = "我今天刚领养了一只橘猫,给它取名叫团子。"
r1 = chat(PC, [{"role": "user", "content": u1}])
print("① 电脑:", u1, "\n   回复:", r1[:80])

u2 = "记住它的名字哦。"
r2 = chat(PC, [{"role": "user", "content": u1}, {"role": "assistant", "content": r1}, {"role": "user", "content": u2}])
print("② 电脑:", u2, "\n   回复:", r2[:80])

admin("POST", "/admin/tick")  # 同步推进:第①轮已确定,写进事实库
print("③ 已推进写入队列")

u3 = "我家那只猫叫什么来着?"
r3 = chat(PHONE, [{"role": "user", "content": u3}])
print("④ 手机:", u3, "\n   回复:", r3[:120])

turns = admin("GET", "/admin/turns")["turns"]
audit = admin("GET", "/admin/audit")["entries"]
assert isinstance(turns, list) and isinstance(audit, list)
last = audit[-1]
assert isinstance(last, dict)
ids = last["memory_ids"]
assert isinstance(ids, list)
print("\n轮状态:", [t["status"] for t in turns])
print("手机这一轮注入的记忆来源:", sorted({i.split(":")[0] for i in ids}))
print("降级来源:", last["degraded_sources"])
ok = "团子" in r3
print("\n结果:", "✅ 换到手机后想起了团子" if ok else "❌ 没想起团子")
sys.exit(0 if ok else 1)
