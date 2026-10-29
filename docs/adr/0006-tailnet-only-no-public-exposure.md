# 记忆网关只在 Tailscale 私网内可达,不暴露公网

记忆网关、两个记忆库都能读写全部记忆与原文日志。我们选择让手机通过 Tailscale 私网访问电脑上的网关,而不是用 Cloudflare Tunnel 开公网地址;两个记忆库只监听本机,只有网关能访问它们。私网内仍保留按设备区分的网关 Token,用于识别设备和兜底鉴权。

## Considered Options

- Cloudflare Tunnel:手机无需常驻 VPN,但需自有域名,网关暴露公网只靠 Token 防护,国内访问 Cloudflare 不稳定。
- 仅局域网:出门不可用。

## Consequences

- 手机必须常开 Tailscale;Android 同时只能有一个 VPN,与 Clash 等代理冲突,前提是用户手机不常驻其他 VPN。
- 实现(2026-10-29,工单 03):Tailscale 不装在 Windows 上,而是作为 Compose 里的容器运行,网关用 `network_mode: service:tailscale` 共用它的网络,手机访问 `http://dafeiyu:8080`。这样避开了 Windows 端口转发(Win10 22H2 的 WSL 不支持 mirrored 网络模式)、管理员防火墙规则,也不怕 WSL 重启后 IP 变化。私网内只监听网关的 8080;不发布子网路由,也不做出口节点,所以两个记忆库从私网不可达。代价:Tailscale 容器重启时,网关也会跟着断网。
