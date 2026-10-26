# 小猫审阅与接手入口

**2026-10-14 起，主线是[小猫核心方案](xiaomao-core-plan.md)。** 它是经用户确认的白话版方案：小猫的定位是个人项目进度秘书，先做「看得懂、准时」的晚报和四档代码状态。

- [小猫核心方案](xiaomao-core-plan.md)：先读这份
- [接手说明（Claude Code / Codex 通用）](claude-code-start-here.md)：阶段 1 的具体任务
- [项目架构](../../ARCHITECTURE.md)
- [贡献与验证要求](../../CONTRIBUTING.md)
- [重设计方案（参考）](xiaomao-agent-redesign.md)：其中 P0 的锁修复和只读原则仍有效；任务系统、协调进程、导出包和自动派发（P1 导出部分与 P2/P3）**暂不做**

- [P0 可靠性开发任务](p0-reliability.md)：阶段 1 第一项「`database is locked`」的详细任务与验收

**2026-10-26 起，`main` 是唯一开发主线**，已包含 v0.2.2、优化方案和文档交接模块。原 `codex/xiaomao-optimization-20261013` 分支、`xiaomao-agent-cloud` 云端仓库和 `PersonalProjects` 副本均已停用，新工作一律从 `main` 切分支。

本机正式调度仍运行 `release/v0.2.2`（`adc2bf9`）；文档交接 MCP 运行 `codex/xiaomao-document-handoff`（`2144a11`）的检出。两者都早于 main，是否按 main 发新版本由用户决定。

文档中的 `~` 指 Mac Studio（小猫运行机）上的用户目录。远程审阅者应以实际克隆目录和显式测试 home 为准，不得根据本机历史路径自动读取其他项目、运行数据或私人资料。
