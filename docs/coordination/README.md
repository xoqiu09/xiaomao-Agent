# 小猫审阅与接手入口

**2026-10-14 起，主线是[小猫核心方案](xiaomao-core-plan.md)。** 它是经用户确认的白话版方案：小猫的定位是个人项目进度秘书，先做「看得懂、准时」的晚报和四档代码状态。

- [小猫核心方案](xiaomao-core-plan.md)：先读这份
- [接手说明（Claude Code / Codex 通用）](claude-code-start-here.md)：阶段 1 的具体任务
- [项目架构](../../ARCHITECTURE.md)
- [贡献与验证要求](../../CONTRIBUTING.md)
- [重设计方案（参考）](xiaomao-agent-redesign.md)：其中 P0 的锁修复和只读原则仍有效；任务系统、协调进程、导出包和自动派发（P1 导出部分与 P2/P3）**暂不做**

代码基线为 v0.2.2 `adc2bf988b462da5498a21321a044a938139a061`。目前所有新增内容都是设计和维护说明，方案中的新命令还没有实现，正式安装与后台调度沿用现有版本。

文档保存与跨 Agent 交接能力另在 [codex/xiaomao-document-handoff](https://github.com/xoqiu09/xiaomao-Agent/tree/codex/xiaomao-document-handoff)，提交 `2144a11149d956c24c348e8fe59ff08c46e109e7`。该能力尚未整合进运行代码，不能视为 v0.2.2 已经具备。

GitHub 的 main 是旧的运行基础。评审最新运行代码请使用 `codex/xiaomao-optimization-20261013`，不要从 main 推断本机运行版本。

文档中的 `~` 指 Mac Studio（小猫运行机）上的用户目录。远程审阅者应以实际克隆目录和显式测试 home 为准，不得根据本机历史路径自动读取其他项目、运行数据或私人资料。
