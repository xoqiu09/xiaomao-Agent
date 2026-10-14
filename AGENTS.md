# 小猫 Agent 优化项目

本目录专用于小猫 Agent 的持续优化。用户选择的第一阶段是整体体检，按实际问题排优先级；后续以用户指定的问题和体检结论逐项推进。

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

优先排查工作树读取超时和日报覆盖缺口，再改进覆盖状态解释、生成延迟、源码运行说明与文档交接分支的整合。

## GitHub account and write checks (2026-10-02)

- For personal GitHub pushes, the expected remote is `git@github-personal:xoqiu09/<repo>.git`. Before a push or PR, verify the absolute repository path (`git rev-parse --show-toplevel`), current branch, HEAD, `git status --short`, every fetch/push remote, and the selected push URL. Use `remote.pushDefault` when configured; otherwise inspect `origin`'s push URL. `source-*` and `legacy` remotes are source/history references and are not the default write target.
- Verify the effective Git author and committer identity with `git var GIT_AUTHOR_IDENT` and `git var GIT_COMMITTER_IDENT`; for xoqiu09 writes, confirm they resolve to the intended personal identity before committing. Do not change global Git, SSH, or account configuration as part of a repository task.
- Before each personal GitHub API or PR write, select the CLI you will use and immediately run its `auth status` and `api user --jq .login`; the login must be `xoqiu09`. Prefer `/Users/xiuqiu/.local/bin/gh-personal`; ordinary `gh` may be used only after the same live identity check. Stop on an identity mismatch. Do not infer CLI/API identity from SSH success or a previous session.
- Use the verified CLI for personal PR commands and pass `--repo xoqiu09/<repo>` to every command; for REST API writes, explicitly address `repos/xoqiu09/<repo>/...`. CLI account and token validity are runtime state: do not assign ordinary `gh` to a fixed account or assume its token is expired. Do not log in, switch accounts, or repair credentials as part of a repository task.
