#!/usr/bin/env bash
# 在 WSL (Ubuntu 24.04) 里安装 Docker Engine + Compose 插件。需要 sudo。
# 用法:sudo bash deploy/install-docker-wsl.sh
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then echo "请用 sudo 运行"; exit 1; fi
TARGET_USER="${SUDO_USER:-admini}"

# 1. Docker 官方 apt 源(download.docker.com 在本机可直连)
apt-get update -y
apt-get install -y ca-certificates curl
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# 2. 让当前用户不用 sudo 就能用 docker
usermod -aG docker "$TARGET_USER"

# 3. 现在就启动 dockerd(本机 WSL 尚未启用 systemd;开机自启在工单 #4 里处理)
service docker start || true
sleep 3
docker version --format 'Docker {{.Server.Version}} 已运行'
echo "完成。新开的终端里 ${TARGET_USER} 可直接用 docker;当前终端可先用:sg docker -c 'docker ps'"
