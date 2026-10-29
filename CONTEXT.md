# 大肥鱼 (dafeiyu)

以 Kelivo 为对话客户端、Ombre-Brain 与 Hindsight 为长期记忆基础的情感陪伴 AI,Windows 与 Android 双端使用。

## Language

### 记忆保证

**执行保证 (Execution Guarantee)**:
每一轮对话都一定发生一次召回与一次写入,不取决于模型是否自愿调用工具。
_Avoid_: 自动记忆, 强制调用

**持久保证 (Durability Guarantee)**:
每一轮对话原文都一定落入原文日志;任一记忆库写入失败都可从原文日志重放补齐。
_Avoid_: 备份, 不丢数据

**相关性保证 (Relevance Guarantee)**:
召回内容恰好是当下需要的那几条。本项目不承诺此项为 100%,只要求尽力且可审计。
_Avoid_: 准确召回, 不出错

**可审计 (Auditable)**:
每一轮注入了哪些记忆(按记忆 ID)都有记录,能事后追溯某次答错的原因。

### 记忆构成

**原文日志 (Transcript Log)**:
逐轮、逐字保存的对话原文,是全部记忆的唯一真源。
_Avoid_: 聊天记录, 历史

**记忆库 (Memory Store)**:
从原文日志派生、可重建的检索索引。本项目有两个:事实库与情感库。
_Avoid_: 数据库, 记忆系统

**事实库 (Fact Store)**:
由 Hindsight 承担,负责人名、关系、日期、偏好、计划等客观事实。

**情感库 (Affect Store)**:
由 Ombre-Brain 承担,负责带情绪坐标的事件、感受与关系连续性。

**关键事实 (Critical Fact)**:
绝不允许说错的事实(如生日、重要的人、重大事件),不经检索,每轮确定性注入。
_Avoid_: 重要记忆, 核心记忆

**核心档案 (Core Profile)**:
关键事实的集合,作为每轮固定注入的内容。
_Avoid_: 用户画像, 人设
