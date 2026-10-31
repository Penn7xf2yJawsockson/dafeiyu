# 大肥鱼 (dafeiyu)

大肥鱼是一个情感陪伴 AI。对话客户端用 Kelivo(Windows 和 Android 两端),电脑上也可以用 DSH(DeepSeek Harness 桌面版,见 [docs/setup-dsh.md](docs/setup-dsh.md)),长期记忆由两个库承担:Hindsight 记事实,Ombre-Brain 记情感。项目的核心是一个**记忆网关**,它保证每一轮对话都一定会召回记忆、一定会写入记忆。术语见 [CONTEXT.md](CONTEXT.md),每个决定的理由见 [docs/adr/](docs/adr/)。人设、核心档案、网关世界书在 [面板](docs/panel.md) 里改。

## 一轮对话的流程

```
Kelivo(电脑 / 手机,经 Tailscale)、DSH(电脑)
  │  OpenAI /v1/chat/completions  或  Anthropic /v1/messages
  ▼
记忆网关 (FastAPI)
  1. 用设备 Token 识别是哪台设备,把这一轮写进原文日志,状态为「待定」
  2. 上一轮这时算确定了 → 写入 Hindsight
  3. 召回(超时约 3 秒,超时就是降级轮):
       - 段的第一轮:Ombre breath + 跨设备衔接(另一台设备 6 小时内的最后 6 轮)
       - 每一轮:Hindsight recall + Ombre breath_search(mode=automatic) + 网关世界书关键词
  4. 组装请求:[人设 + 核心档案][Kelivo 发来的历史][召回块,深度 1][最新消息]
  5. 转发给上游(DeepSeek 官方或中转,同时只用一个),把回复流式返回给 Kelivo
  6. 记录审计:这一轮用了哪些记忆 ID,是否降级
  段结束(空闲 30 分钟或满 20 轮)→ 整段交给 Ombre grow,写入情感记忆
```

## 保证什么

- **执行保证**:每一轮一定发起召回和写入,结果是成功还是降级都会记录。
- **持久保证**:原文日志先落盘,两个库写入失败可以从日志重放。
- **相关性保证**:不承诺 100%,只保证可审计。关键事实不靠检索,每轮都注入。

## 不在仓库里的东西

这个仓库是公开的。密钥、原文日志、人设、核心档案、网关世界书、两个库的数据都放在 `data/`,已被 `.gitignore` 排除,每天备份到 E 盘。
