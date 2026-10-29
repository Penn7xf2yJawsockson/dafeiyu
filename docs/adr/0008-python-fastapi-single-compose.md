# 记忆网关用 Python + FastAPI,三个服务放在同一个 Docker Compose

Hindsight 与 Ombre-Brain 都有 Python 生态(Hindsight 有官方 Python 客户端,Ombre 本身就是 Python),流式转发与 OpenAI/Anthropic 两种格式的转换在 Python 上都有成熟库,因此网关用 Python + FastAPI 编写,确认面板也放在同一服务里。网关、Hindsight(含 PostgreSQL)、Ombre 写在同一个 Docker Compose 中,运行在 Windows 的 WSL 内;两个记忆库只在 Compose 内网可达,只有网关端口经 Windows 端口转发给 Tailscale。
