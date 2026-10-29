# 记忆侧模型:DeepSeek 抽取打标,硅基流动 bge-m3 向量化与 bge-reranker-v2-m3 重排

两个记忆库共用硅基流动的 `BAAI/bge-m3`(1024 维,免费档)做向量化,Hindsight 用硅基流动的 `BAAI/bge-reranker-v2-m3` 做重排;Hindsight 抽取事实与 Ombre-Brain 打标都用 `deepseek-flash`(打标关闭 thinking,`max_tokens` 4096)。没有用 Hindsight 的本地默认模型,因为默认向量模型 `bge-small-en-v1.5` 与默认重排模型 `ms-marco-MiniLM-L-6-v2` 都只适用于英文,而对话是中文。

## Considered Options

- 本地 Ollama 跑 bge-m3:不依赖网络,但要多维护一个本地服务;最终选择云端免费档,每轮召回依赖硅基流动的可用性,不可用时按降级轮处理。
- Qwen3-Embedding-8B:质量更高但付费、向量大 4 倍,对短文本记忆收益有限。
- Gemini 2.0 Flash(Ombre README 推荐的打标模型):已于 2026-06-01 停止服务。

## Consequences

- 更新(2026-10-29):用户只用中转,不用 DeepSeek 官方 Key。事实抽取与情感打标默认和陪聊共用同一个上游(同一中转、同一模型、同一 Key),需要时可用 `MEMORY_LLM_*` 单独指定。中转转发 `thinking: disabled` 是否生效,以契约测试为准。
- 更换向量模型意味着两个库的全部记忆都要重新向量化,不要轻易更换。
- `Pro/BAAI/bge-m3` 可作为免费档受限时的备用,但部署时须先用同一段文本分别请求两者,确认向量一致后才能混用。
