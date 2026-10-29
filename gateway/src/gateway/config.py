"""网关配置。密钥只从环境变量或 .env 读取,不进仓库。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    # 网关 Token → 设备名(ADR-0004:设备靠各自的 Token 区分)
    devices: dict[str, str]
    # 上游任一时刻只有一个(CONTEXT.md「上游」)
    upstream_base_url: str
    upstream_api_key: str
    upstream_model: str
    # 面板与管理接口的 Token;为空时管理接口全部拒绝
    admin_token: str = ""
    companion: str = "dafeiyu"
    timezone: str = "Asia/Shanghai"   # 召回块里"现在是几点"用的时区
    recall_timeout_seconds: float = 3.0
    episode_idle_minutes: float = 30.0
    episode_max_turns: int = 20
    handoff_window_hours: float = 6.0
    handoff_turns: int = 6
    upstream_timeout_seconds: float = 120.0
    # 后台循环:结束空闲的段、重试写入队列。测试关闭,改用 /admin/tick 手动推进
    background_worker: bool = True
    background_interval_seconds: float = 30.0
    # 抓包开关:>0 时保留最近 N 次原始请求到 data_dir/debug(排查 Kelivo 实际发了什么)
    debug_capture: int = 0
