# 记忆网关只在 Tailscale 私网内可达,不暴露公网

记忆网关、两个记忆库都能读写全部记忆与原文日志。我们选择让手机通过 Tailscale 私网访问电脑上的网关,而不是用 Cloudflare Tunnel 开公网地址;两个记忆库只监听本机,只有网关能访问它们。私网内仍保留按设备区分的网关 Token,用于识别设备和兜底鉴权。

## Considered Options

- Cloudflare Tunnel:手机无需常驻 VPN,但需自有域名,网关暴露公网只靠 Token 防护,国内访问 Cloudflare 不稳定。
- 仅局域网:出门不可用。

## Consequences

- 手机必须常开 Tailscale;Android 同时只能有一个 VPN,与 Clash 等代理冲突,前提是用户手机不常驻其他 VPN。
- Win10 22H2 的 WSL 不支持 mirrored 网络模式,Tailscale 装在 Windows 宿主上时,需要把宿主端口转发到 WSL 内的网关。
