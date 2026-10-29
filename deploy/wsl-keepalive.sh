#!/usr/bin/env bash
# 开机自启(工单 03):由 Windows 登录计划任务以 root 调起,常驻不退出。
# 1. 启动 dockerd(本机 WSL 没启用 systemd)
# 2. 拉起 Compose(容器都是 restart: unless-stopped,这一步只是保险)
# 3. 常驻:有进程挂着,WSL 就不会因为空闲而被关掉
set -u
exec 9>/run/dafeiyu-keepalive.lock
flock -n 9 || exit 0   # 已经有一个在跑了

REPO="$(cd "$(dirname "$0")/.." && pwd)"
LOG=/var/log/dafeiyu-keepalive.log
echo "$(date -Is) keepalive 启动" >> "$LOG"

service docker status >/dev/null 2>&1 || service docker start >> "$LOG" 2>&1
for _ in $(seq 1 30); do docker info >/dev/null 2>&1 && break; sleep 1; done
(cd "$REPO" && docker compose up -d >> "$LOG" 2>&1) || echo "$(date -Is) compose up 失败" >> "$LOG"

exec sleep infinity
