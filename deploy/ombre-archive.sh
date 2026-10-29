#!/usr/bin/env bash
# 把一条 Ombre 情感记忆移进归档(从召回和浮现里彻底拿掉;文件仍留在 data/ombre/archive,可以恢复)。
#
# 用法(在仓库根目录):deploy/ombre-archive.sh <bucket_id> [<bucket_id>...]
# 恢复:              deploy/ombre-archive.sh --restore <bucket_id>
#
# 为什么不用 Dashboard 的「归档」按钮:它只生成一张待 AI 审批的删除申请,要由能调用 trace 的 AI 批准,
# 而大肥鱼聊天时不会拿到这个工具,申请会一直挂着。这里直接以记忆库管理者身份调用 trace。
# 不需要打开管理模式;走网关容器里的 Ombre 地址和 Token。
set -euo pipefail

action=delete
if [[ "${1:-}" == "--restore" ]]; then action=restore; shift; fi
if [[ $# -eq 0 ]]; then sed -n '2,9p' "$0"; exit 2; fi

for id in "$@"; do
  [[ "$id" =~ ^[0-9a-f]{12}$ ]] || { echo "不是 bucket_id:$id(应为 12 位十六进制)" >&2; exit 2; }
done

docker exec -i -e ACTION="$action" -e OMBRE_URL="${OMBRE_URL:-}" dafeiyu-gateway-1 python - "$@" <<'PY'
import json, os, sys
import httpx

url = os.environ.get("OMBRE_URL") or os.environ["OMBRE_MCP_URL"]
headers = {"Authorization": f"Bearer {os.environ['OMBRE_MCP_TOKEN']}", "Accept": "application/json, text/event-stream"}
flag = {"delete": True} if os.environ["ACTION"] == "delete" else {"restore": True}
failed = False
for i, bucket_id in enumerate(sys.argv[1:], 1):
    body = {"jsonrpc": "2.0", "id": i, "method": "tools/call",
            "params": {"name": "trace", "arguments": {"bucket_id": bucket_id, **flag}}}
    text = httpx.post(url, json=body, headers=headers, timeout=60).text
    data = json.loads(text[text.find("{"):])
    result = data.get("result") or {}
    msg = "".join(c.get("text", "") for c in result.get("content", []))
    if "error" in data or result.get("isError"):
        failed = True
        print(f"{bucket_id}: 失败 {data.get('error') or msg}")
    else:
        print(f"{bucket_id}: {msg.strip()}")
sys.exit(1 if failed else 0)
PY
