# 在 DSH(DeepSeek Harness 桌面版)里用大肥鱼

DSH 走电脑本机的网关入口 `http://127.0.0.1:18080/v1`,和电脑上的 Kelivo 共用同一个设备 Token(同一台设备,记忆是同一份)。

## 配置

编辑 `C:\Users\<你>\.dsh\profiles\desktop\cordis.patch.yml`,加两条(已有同 id 的条目就改它):

```yaml
- id: llm-pi-ai
  name: "@deepseek-ai/dsh-llm-pi-ai"
  config:
    providers:
      xiaoyi:
        displayName: xiaoyi
        apiKeyEnv: XIAOYI_API_KEY        # 值填在 DSH 设置里,存进 .dsh/.credentials.yaml,不写进这个文件
        api: openai-completions
        baseURL: http://127.0.0.1:18080/v1
        models:
          - id: dafeiyu                  # 带记忆
            name: dafeiyu
            input: [text, image]
          - id: dafeiyu-direct           # 不带记忆的直连
            name: dafeiyu-direct
- id: agent-default-model
  name: "@deepseek-ai/dsh-agent-default-model"
  config:
    provider: xiaoyi
    model: dafeiyu
```

`XIAOYI_API_KEY` 的值就是 `.env` 里电脑那台设备的 Token。

## 网关对 DSH 做了什么特殊处理

- **时间上下文**:DSH 每隔一段时间(默认 10 分钟)在请求末尾追加一条 `Time sampled while preparing turn N, step M: …` 用户消息。网关不把它当成用户说的话,不拿去召回、不写进记忆,但会原样发给上游模型,所以模型仍然知道当前时间。
- **起会话标题**:DSH 起标题和更新标题的请求直接转发给上游,不召回、不记成一轮。
- **压缩(compaction)**:压缩请求直接转发;同时和 Kelivo 一样,网关先结束当前这一段对话,写进两个记忆库,压缩后的第一轮按新一段处理。

这些都是按 DSH 2.0.18 随包插件的默认提示词识别的。DSH 升级后如果改了这些提示词,在 `/admin/turns` 里会看到它们又被记成了对话,到时候更新 `gateway/src/gateway/background.py` 里的 `DSH_BACKGROUND_PROMPTS`。

## 怎么检查

```bash
T=$(grep '^GATEWAY_ADMIN_TOKEN=' .env | cut -d= -f2-)
curl -s -H "Authorization: Bearer $T" http://127.0.0.1:18080/admin/turns | python3 -m json.tool | tail -40
```

DSH 里说一句话之后,最后一条 turn 的 `user_text` 应该就是你说的那句话,不带 `Time sampled…`,也不会多出一条 `Generate the session title…`。
