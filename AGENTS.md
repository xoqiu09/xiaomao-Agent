# 小猫 Agent 优化项目

本目录专用于小猫 Agent 的持续优化。主线见 [docs/coordination/xiaomao-core-plan.md](docs/coordination/xiaomao-core-plan.md)，当前任务见 [docs/coordination/claude-code-start-here.md](docs/coordination/claude-code-start-here.md)。

## 产品方向（2026-10-14 用户确认）

- **定位**：小猫是用户的个人项目进度秘书。它主动告诉用户做了什么、多了哪些核心功能、做到哪、代码在哪；开工时告诉用户从哪继续。它不替用户写代码，也不派活。
- **四档状态**：给用户看的每条变化都要标出「未提交 / 仅本机 / 已推送 / 已合并」之一。用户在 Mac Studio（小猫运行机）和 MacBook Pro 两台机器上开发，通过 GitHub 同步。
- **主动汇报**：晚报（21:45 前送达，规则版先发）、早上开工提醒、未推送提醒、每周进度。
- **面向用户的文字**：白话，最多 3 个项目、每个最多 3 条，不出现「未核实 / 范围哈希 / 观察 ID / 覆盖 partial / 待确认」这类术语。严谨的证据放在依据文件里，不放进正文。
- **暂不做**：自动派发 Claude Code / Codex、任务系统（coordination.sqlite / TaskSpec）、常驻协调进程、TASK/HANDOFF/STATE 导出包。如确有需要，先在核心方案中写明理由并经用户确认。
- **和用户沟通**：用白话说明「做了什么、用户会看到什么变化」，不要用内部术语汇报。方案出现分歧时，先问用户想要什么，不要自行扩大范围。

## 源码与运行身份

- 优化目录：`/Users/xiuqiu/WorkSpace/xiaomao-Agent-optimization`。
- 初始分支：`codex/xiaomao-optimization-20261013`，起点为 `v0.2.2`，完整提交 `adc2bf988b462da5498a21321a044a938139a061`。
- 原开发目录：`/Users/xiuqiu/WorkSpace/xiaomao-Agent`。该目录与 `PersonalProjects` 下的旧副本使用各自分支，不能代替本目录的优化成果。
- 2026 年 10 月 13 日核对时，正式调度指向 `/Users/xiuqiu/Library/Application Support/Xiaomao/release/v0.2.2`。源码中的 `EXECUTION_STATE.md` 尚有旧版本说明；运行状态须重新核对。
- 当前远端为个人仓库 `xoqiu09/xiaomao-Agent`。提交、推送、合并与切换正式安装分别记录；整体体检不包含这些动作。

## 工作方式

先阅读 `README.md`、`ARCHITECTURE.md`、`CONTRIBUTING.md`，核对分支、完整 HEAD、差异和远端。仅在本优化目录实现候选改动；保留原开发树、发布树、正式配置、数据库及历史报告。

体检发现须附可定位的证据、用户影响、优先级和验收标准。区分已确认缺陷、观察到的运行问题及尚待复现的原因，不能把模型服务可连接写成模型效果验证通过。

保持小猫的个人项目读取边界，不为体检读取公司源码或历史公司正文。模型只接收程序筛选的事实；采集和查询不加载模型，失败时保留规则结果。代码截断、权限或读取超时、首次基线与实际采集空档须分别说明。

用临时 home 和合成个人 Git 仓复现问题。有现成行为时先验证，不人为制造失败。改动完成后运行仓库已有的隔离验收：

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /Users/xiuqiu/.local/bin/python3.11 scripts/accept.py --isolated
```

隔离测试、正式 home、外盘身份、模型语义质量、通知实际可见性和多日稳定性分别给出结论。运行入口、数据或调度的变更属于后续发布工作，应先准备已验证候选和可恢复备份。

## 第一轮体检资料

本轮报告和证据保存在 `/Users/xiuqiu/Documents/Codex/2026-10-13/bang/outputs/`：`xiaomao-agent-health-report.md`、`xiaomao-health-evidence.json`、`xiaomao-isolated-acceptance.json`。

2026-10-14 起，优先级以核心方案的阶段 1 为准：
1. 复现并修复 `database is locked`
2. 四档状态
3. 白话晚报
4. 规则版先发、模型后补
5. 通知带内容

工作树读取超时和覆盖缺口继续记录在依据文件里，不再向用户正文展示这些解释。

## GitHub account and write checks (2026-10-02)

- For personal GitHub pushes, the expected remote is `git@github-personal:xoqiu09/<repo>.git`. Before a push or PR, verify the absolute repository path (`git rev-parse --show-toplevel`), current branch, HEAD, `git status --short`, every fetch/push remote, and the selected push URL. Use `remote.pushDefault` when configured; otherwise inspect `origin`'s push URL. `source-*` and `legacy` remotes are source/history references and are not the default write target.
- Verify the effective Git author and committer identity with `git var GIT_AUTHOR_IDENT` and `git var GIT_COMMITTER_IDENT`; for xoqiu09 writes, confirm they resolve to the intended personal identity before committing. Do not change global Git, SSH, or account configuration as part of a repository task.
- Before each personal GitHub API or PR write, select the CLI you will use and immediately run its `auth status` and `api user --jq .login`; the login must be `xoqiu09`. Prefer `/Users/xiuqiu/.local/bin/gh-personal`; ordinary `gh` may be used only after the same live identity check. Stop on an identity mismatch. Do not infer CLI/API identity from SSH success or a previous session.
- Use the verified CLI for personal PR commands and pass `--repo xoqiu09/<repo>` to every command; for REST API writes, explicitly address `repos/xoqiu09/<repo>/...`. CLI account and token validity are runtime state: do not assign ordinary `gh` to a fixed account or assume its token is expired. Do not log in, switch accounts, or repair credentials as part of a repository task.
