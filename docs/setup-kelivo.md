# Kelivo 接入(电脑 + 手机)

## 1. 让手机能连上(只做一次)

0. Tailscale 容器默认不启动。在 `.env` 里加一行 `COMPOSE_PROFILES=phone`,然后运行 `docker compose up -d`。
1. 电脑 WSL 里运行 `docker compose logs tailscale | grep login.tailscale.com`,用浏览器打开最后那个链接,登录你的 Tailscale 账号。这台机器会以 **dafeiyu** 的名字加入你的私网。
   - 也可以在 Tailscale 后台生成一个 Auth Key,填进 `.env` 的 `TS_AUTHKEY=`,然后运行 `docker compose up -d tailscale`。
2. 手机安装 Tailscale:Google Play、F-Droid,或 https://pkgs.tailscale.com/stable/ 下载 APK。用**同一个账号**登录。
3. Android 同一时间只能开一个 VPN。用大肥鱼时要开着 Tailscale,和 Clash 之类的代理不能同时开。

## 2. 在 Kelivo 里加供应商

两台设备各配一次。API Key 用各自的 Token,在电脑上 `~/dafeiyu/.env` 文件的 `GATEWAY_DEVICES=pc=…,phone=…` 这一行里。

| 项 | 电脑 | 手机 |
|---|---|---|
| 供应商类型 | OpenAI 兼容 | OpenAI 兼容 |
| Base URL | `http://127.0.0.1:18080/v1` | `http://dafeiyu:8080/v1` |
| API Key | `pc=` 后面那一串 | `phone=` 后面那一串 |
| 模型 | `dafeiyu`(可以点"获取模型") | `dafeiyu` |

- 手机上 `dafeiyu` 这个名字解析不了时,把它换成 Tailscale 里显示的 `100.x.x.x` 地址。
- 想用 Claude 类型的供应商也可以:Base URL 同上,不带 `/v1`,网关也提供 `/v1/messages`。

## 3. 模型设置(两台都要)

- 供应商里的 `dafeiyu` 模型 → 编辑 → **能力** → 打开 **「工具」**。Kelivo 按模型名猜能力,认不出 `dafeiyu`;不打开就不会把联网搜索、网页抓取等工具发给模型。
- 设置 → 默认模型 → **标题生成模型**(以及开启了的**聊天建议模型**、**翻译模型**等)选 `dafeiyu-direct`。这些后台请求不该进记忆;网关能识别 Kelivo 的默认提示词作为兜底,但如果你改过这些提示词,只能靠这里的设置。

## 4. 助手设置(两台都要)

- **记忆:关闭**,`chat_search` 工具也关闭。记忆完全由网关负责(Q10)。
- **系统提示词:留空**。人设和核心档案只在网关保存(`data/gateway/persona.md`、`core_profile.md`)。Kelivo 自动加的技能列表、搜索引用规则会照常保留。
- **世界书:不用**。关键词条目由网关世界书负责(工单 09)。
- **流式输出:开启**。

## 5. 什么时候压缩上下文

- 看 Kelivo 输入栏的上下文用量,**超过 60–70% 就点一次「压缩上下文」**。Kelivo 只会手动压缩,不会自己压。
- 压缩不会丢记忆:网关每轮都先把原文记下。压缩请求一到,网关会立刻把这一段写进事实库和情感库,压缩后的第一轮从新一段开始,重新浮现情感记忆,补上模型看不到的上下文。摘要本身不进记忆。
- **不要用「上下文消息数量」限制历史**:它每轮都丢掉最早一条,历史开头每轮都在变,DeepSeek 的缓存会每轮失效,比压缩一次贵得多。
- 压缩模型可以选 `dafeiyu` 或 `dafeiyu-direct`,效果一样;网关按 Kelivo 默认的压缩提示词识别。改过压缩提示词的话,网关就认不出了,只能等这一段空闲 30 分钟后自动写入。

## 6. 自动启动

`deploy/windows/install-autostart.ps1` 已经注册了 Windows 计划任务 `dafeiyu-autostart`。**登录 Windows 后**,它会在后台(不弹窗)启动 WSL、Docker 和全部服务,并让 WSL 一直开着,不会因为空闲被关掉。

- 只有 Windows **登录后**才会启动。想让电脑开机就能用,需要开启 Windows 自动登录(例如 `netplwiz` 里取消"要使用本计算机,用户必须输入用户名和密码")。
- Windows 自动更新重启后也一样:登录之后才会恢复。
- 启动日志在 WSL 的 `/var/log/dafeiyu-keepalive.log`(属于 root,用 `sudo cat` 查看)。

## 7. 检查是否连通

- 电脑浏览器打开 `http://127.0.0.1:18080/v1/models`。显示 `unknown gateway token`,说明网关在运行;这是正常的,因为浏览器没带 Token。
- 手机开着 Tailscale,用浏览器打开 `http://dafeiyu:8080/v1/models`,应该看到同样的提示。
