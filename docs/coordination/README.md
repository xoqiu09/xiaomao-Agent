# 小猫重设计审阅入口

本分支以正式运行代码 v0.2.2 为基础，增加重设计方案与接手说明。先审阅方案和现有实现，第一轮实施范围为可靠性基础与项目接续。

- [Claude Code 接手说明](claude-code-start-here.md)
- [完整重设计方案](xiaomao-agent-redesign.md)
- [项目架构](../../ARCHITECTURE.md)
- [贡献与验证要求](../../CONTRIBUTING.md)

代码基线为 `adc2bf988b462da5498a21321a044a938139a061`。本次新增的是设计与维护说明，候选命令尚未实现，正式安装与后台调度沿用现有版本。

文档保存与跨 Agent 交接能力另在 [codex/xiaomao-document-handoff](https://github.com/xoqiu09/xiaomao-Agent/tree/codex/xiaomao-document-handoff)，提交 `2144a11149d956c24c348e8fe59ff08c46e109e7`。该能力尚未整合进本分支的运行代码；评审应同时比较这条分支，不能把独立能力直接视为 v0.2.2 已具备。

GitHub 的 main 包含旧运行基础与账号写入检查；评审最新运行代码请使用 `codex/xiaomao-optimization-20261013`，不要从 main 推断本机运行版本。

文档中的 `~` 表示原作者机器的用户目录。远程审阅者应以实际克隆目录和显式测试 home 为准；不得根据本机历史路径自动读取其他项目、运行数据或私人资料。
