# 记忆网关只在 Tailscale 私网内可达,不暴露公网

记忆网关、两个记忆库都能读写全部记忆与原文日志。我们选择让手机通过 Tailscale 私网访问电脑上的网关,而不是用 Cloudflare Tunnel 开公网地址;两个记忆库只监听本机,只有网关能访问它们。私网内仍保留按设备区分的网关 Token,用于识别设备和兜底鉴权。

## Considered Options

- Cloudflare Tunnel:手机无需常驻 VPN,但需自有域名,网关暴露公网只靠 Token 防护,国内访问 Cloudflare 不稳定。
- 仅局域网:出门不可用。

## Consequences

- 手机必须常开 Tailscale;Android 同时只能有一个 VPN,与 Clash 等代理冲突,前提是用户手机不常驻其他 VPN。
- 实现(2026-10-29,工单 03):Tailscale 不装在 Windows 上,而是作为 Compose 里的容器运行,用 `tailscale serve` 只把私网的 8080 端口 TCP 转发给网关,手机访问 `http://dafeiyu:8080`。这样避开了 Windows 端口转发(Win10 22H2 的 WSL 不支持 mirrored 网络模式)和管理员防火墙规则,也不怕 WSL 重启后 IP 变化。不发布子网路由,也不做出口节点,所以两个记忆库从私网不可达。网关有自己的网络,`127.0.0.1:18080` 由网关自己发布:Tailscale 没登录、重启或故障时,电脑端照常可用。最初的做法是让网关共用 Tailscale 容器的网络(`network_mode: service:tailscale`),但没登录时 Tailscale 会反复退出重启,每次都会把网关(包括电脑端的端口)一起断开,所以改成了现在这样。
- 管理模式(2026-10-29):用户需要查看、修改、删除记忆。两个记忆库自带的网页界面(Hindsight 控制台、Ombre Dashboard)平时仍不对外;只有手动叠加 `deploy/compose.admin.yml` 时才发布到电脑本机 `127.0.0.1`(9999 / 18001),各自要求登录口令,手机、局域网和 Tailscale 私网都访问不到。用完用 `docker compose up -d` 关闭。说明见 `docs/manage-memories.md`。
