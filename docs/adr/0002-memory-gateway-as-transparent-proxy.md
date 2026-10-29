# 记忆网关以透明代理形式接入,而非 MCP 工具

Kelivo 没有请求前后 hook、没有 tool_choice、没有预置消息,经 MCP 暴露记忆只能靠模型自愿调用,无法满足执行保证。因此在 Kelivo 与上游之间放一层记忆网关:Kelivo 只把它当作一个模型供应商,网关在每一轮确定性地召回、注入、转发、写入。网关同时提供 OpenAI 兼容(`/v1/chat/completions`)与 Anthropic 兼容(`/v1/messages`)两种入口,上游可按配置路由到 DeepSeek 官方或中转。

## Considered Options

- 只在 Kelivo 挂 Ombre/Hindsight 的 MCP:零开发,但召回与写入取决于模型自觉,放弃执行保证。
- 只做 OpenAI 兼容入口:更简单,但用户要求保留 Anthropic 格式。

## Consequences

- 两个记忆库的 MCP 不挂在 Kelivo 上,模型不直接操作记忆库。
- 更新(2026-10-29,实测抓包后):Kelivo 把技能列表、搜索引用规则放在 system 消息里,把工具定义放在 `tools` 字段里。网关保留 Kelivo 的 system 消息,放在人设和核心档案之后;人设仍只在网关保存,Kelivo 端系统提示词留空。
- Kelivo 的后台请求(生成标题、聊天建议、总结、压缩上下文、OCR、翻译)按默认提示词识别,只原样转发,不召回、不记录;另提供无记忆直通模型 `dafeiyu-direct`,供这些后台任务和其他客户端使用。
- DeepSeek 的 Anthropic 兼容端点忽略 `cache_control`,走 B 入口时不能主动控制缓存,只依赖 DeepSeek 的自动前缀缓存。
