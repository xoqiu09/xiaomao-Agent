# 小猫 / xiaomao-agent

小猫是一个跑在你自己 Mac 上的**个人项目进度秘书**。

它每天晚上告诉你三件事：今天哪些项目有进展，每项改动的代码现在在哪，明天从哪继续。它只看你登记过的个人仓库，只读不写。它不替你写代码，也不派活。

## 每天你会收到什么

每晚 21:30 结束当天的统计，大约 21:45 前送达一份晚报，同时弹一条系统通知。下面是一份真实的晚报：

```
小猫晚报 · 11月9日（周一）
今天 2 个项目有进展，2 个项目共 6 个提交还没推送。

★ Session Steward
  · Merge pull request #69 from xoqiu09/codex/ux-round2-20261109  🟢 已合并
  · fix: clarify project selection and adapt town map controls  🟢 已合并
  · Merge pull request #68 from xoqiu09/codex/ux-walkthrough-20…  🟢 已合并
  （另有 3 项变化，见依据）
  （另有 10 处改动还没提交）

★ xiuqiu-site
  · content(radar): 2026-11-09 daily AI and Web3 briefs (#146)  🟢 已合并
  · content(market-radar): 2026-11-09 MEXC order snapshot (#145)  🟢 已合并
  （另有 27 处改动还没提交）

明天从这里继续
  1. QAI：推送本地的 5 个提交
  2. xiuqiu-site：27 处未提交改动，建议分批提交
  3. Wallet-Infrastructure：推送本地的 1 个提交

今天没动：QAI、wallet-core、xiaomao-Agent、AI-Web3-Learning、Wallet-Infrastructure、旷野之心、mini-exchange
7 处采集问题，详见依据 → 菜单「查看依据」
MacBook 上还没推送的工作，小猫看不到。
```

每条改动后面的标记表示代码现在在哪：

| 标记 | 意思 |
| --- | --- |
| 🟡 未提交 | 改了，还没 commit |
| 🟠 仅本机 | commit 了，还没推到 GitHub |
| 🔵 已推送 | 推到 GitHub 了，还没合进主分支 |
| 🟢 已合并 | 已经在 GitHub 的主分支里 |

没有远端仓库的项目只会出现前两种，项目名后面标「（无远端）」。用 rebase 或压缩方式合并的提交，小猫会比对改动内容，同样算作已合并。

## 你会用到的几样东西

| 东西 | 在哪 | 做什么 |
| --- | --- | --- |
| 晚报 | 通知，或 `xiaomao latest daily --print` | 上面那样的白话总结，最多 3 个项目、每个最多 3 条 |
| 依据 | 菜单「查看日报依据」，或 `xiaomao latest daily --details --print` | 晚报背后的完整记录：每个提交的状态和判断依据、采集问题等。晚报只是精简版，依据一条不少 |
| 菜单栏 | 屏幕右上角的小猫图标（SwiftBar 插件） | 今天的摘要、明天先做什么、很久没提交的项目，每分钟刷新 |
| 问小猫 | 菜单「问小猫…」，或 `xiaomao ask "问题"` | 用本机模型回答关于最近几天项目的问题，见下一节 |

### 问小猫

问小猫用的是本机的 Ollama 模型，默认 `qwen3.6:35b`，不连云端。它只看三类资料：

1. 最近几天的晚报记录（默认 7 天，最多 14 天）；
2. 各项目说明里「这个项目是做什么的」那一句；
3. 文档库里最新的几份交接（要在配置里指定文档库位置）。

它不扫描仓库，也不读源代码。回答分三种：

- **项目问题**：按上面的资料回答，并注明依据来自哪天的晚报、哪份说明或交接。没有依据的回答不会显示。
- **常识问题**：模型有把握时直接回答，并标明「这是模型自己的回答，没有依据」。
- **要看代码、做判断或者没把握的问题**：不硬答，改为给你一句可以直接发给 Codex 或 Claude 的问题。

现在每问一次大约要 1 分钟，大部分时间花在加载模型上。菜单里的入口目前是一次性弹窗：问一句、答一句，不能接着追问。改成真正的对话窗口还在调研。

## 小猫不做什么

- **不写你的仓库**：不 fetch、不提交、不跑你项目里的测试、不执行 hooks。只用只读的 Git 命令。
- **不碰公司项目**：只看登记过的个人仓库，公司项目和来源不明的目录不会被采集。
- **扫描不用模型**：每 5 分钟一次的扫描和平时的查询都不加载模型。模型只在补充晚报解读、回答提问时用到，而且只能看到程序挑出来的事实，没有执行命令或写文件的权限。
- **模型出问题不耽误晚报**：先发规则生成的晚报和通知，模型解读完成后再原地补上，标题加「已补充解读」，不会再发第二条通知。
- **看不到另一台电脑**：MacBook 上还没推送的工作，小猫看不到，每份晚报末尾都会提醒一句。

小猫和 Session Steward「小镇」的关系：在小镇里，小猫同样只做进度秘书，只读事实、写自己的解释，不接手任务。详见[核心方案第十节](docs/coordination/xiaomao-core-plan.md)。

## 现在的状态

- **已经能用**：白话晚报、四档代码状态、通知、菜单栏、依据文件、问小猫（命令行和一次性弹窗）。
- **已知问题**：
  - 晚报偶尔晚于 21:45 送达，因为送出前要先完整扫一遍所有项目，大约需要 10 分钟。
  - 后台读取个别工作树的 Git 信息会偶尔超时，晚报里体现为「N 处采集问题」。
  - 本机模型没在运行时，晚报条目直接显示提交原文，没有中文解读。
  - 把多个提交压成一个再合并的情况，原来的提交仍会显示为「已推送」。
- **接下来**：先修上面的问题；再做阶段 2，包括两台电脑的同步、早上开工提醒、未推送提醒；然后是阶段 3，包括功能时间线和每周进度。完整路线见[核心方案](docs/coordination/xiaomao-core-plan.md)。

`pyproject.toml` 里的版本号还是 `0.2.2`，没有随功能更新。正式安装实际运行哪个版本，以本机 `~/Library/Application Support/Xiaomao/release/` 下的目录和两个定时任务的配置为准。

## 安装与运行

### 需要什么

- macOS、Git、Python 3.11 或更高版本。小猫核心只用 Python 标准库，没有第三方依赖。
- 可选：[SwiftBar](https://github.com/swiftbar/SwiftBar)（菜单栏）、Ollama 和本地模型（晚报解读、问小猫）。

### 先在临时目录试一下

不需要安装，直接从源码运行。试用时一定要用 `--home` 指定一个临时目录，免得写进正式数据。

```bash
export PYTHONPATH="$PWD/src"
python3.11 -m xiaomao --home /tmp/xiaomao-try init
python3.11 -m xiaomao --home /tmp/xiaomao-try doctor
python3.11 -m xiaomao --home /tmp/xiaomao-try projects discover --root /path/to/your/projects
```

下面的命令都假设已经 `pip install -e .` 装好了 `xiaomao` 命令；没装的话，把 `xiaomao` 换成 `python3.11 -m xiaomao` 即可。

### 常用命令

| 想做什么 | 命令 |
| --- | --- |
| 看最新晚报 / 依据 | `xiaomao latest daily --print` / `xiaomao latest daily --details --print` |
| 生成某天的晚报（不发通知、不用模型） | `xiaomao daily --date 2026-11-09` |
| 生成并让模型补充解读 | `xiaomao daily --with-model` |
| 问问题 | `xiaomao ask "这周 xiuqiu-site 做了什么"`，可加 `--days 3`、`--project 项目ID`、`--model 模型名` |
| 手动扫描一次 | `xiaomao scan` |
| 看运行是否正常 | `xiaomao health` |
| 暂停 / 恢复扫描或模型 | `xiaomao pause scan`、`xiaomao resume scan`（模型用 `infer`） |
| 登记和调整项目 | `xiaomao projects discover`、`xiaomao projects configure --project 项目ID --worktrees all` |
| 设置晚报时间和通知 | `xiaomao projects configure --time 21:30 --timezone Asia/Taipei --notify on` |
| 跨 Agent 保存文档和交接 | `xiaomao docs ...`，见[文档交接说明](docs/DOCUMENT_HANDOFF.md) |

### 数据放在哪

- 正式数据目录是 `~/Library/Application Support/Xiaomao/`，里面有 `config.json`（登记了哪些项目）、`xiaomao.sqlite`（采集记录）和 `reports/`（晚报、依据、菜单内容）。
- 可以用 `--home` 参数或环境变量 `XIAOMAO_HOME` 换一个目录。
- 定时任务是两个用户级 LaunchAgent：扫描每 5 分钟一次；晚报每天 21:30 生成，另外每 5 分钟检查一次有没有错过的晚报需要补发。
- 本地模型放在外接盘上，路径在 `config.json` 的 `external` 一项里。小猫不会把模型下载到内置盘。

### 升级正式安装

正式安装用的是一份固定的发布目录，`main` 上的改动不会自动生效。升级步骤是：

1. 在新目录跑隔离验收；
2. 备份配置、定时任务、菜单链接、报告和数据库；
3. 再把两个定时任务和菜单插件切到新目录。

每份备份里都附带回退脚本。详见[运行切换说明](docs/RUNTIME_0.2.md)。

## 开发

### 测试

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3.11 -m unittest discover -s tests
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3.11 scripts/accept.py --isolated
```

第二条是隔离验收：在临时目录里用真实的临时 Git 仓库跑一遍完整流程，并检查你的仓库文件、暂存区和 hooks 都没有被改动。它通过只说明隔离环境没问题；正式数据目录和外接盘那两项永远显示 `NOT_RUN`，需要另外验证。

修改流程和注意事项见 [CONTRIBUTING.md](CONTRIBUTING.md)。

### 目录

```text
src/xiaomao/            核心代码：命令行入口 cli.py、采集、存储、晚报、四档状态、问小猫、菜单渲染、文档交接
tests/                  测试，全部使用临时目录和合成的 Git 仓库
scripts/                隔离验收、两个定时任务的入口脚本、问小猫弹窗、Ollama 启停
scripts/swiftbar/       菜单栏插件和图标
integrations/skills/    给 Agent 用的文档交接技能
docs/                   各功能的详细说明
docs/coordination/      核心方案（产品主线）、接手说明
```

### 进一步阅读

- [核心方案](docs/coordination/xiaomao-core-plan.md)：小猫要做什么、不做什么、分几个阶段。**改功能前先读这份。**
- [接手说明](docs/coordination/claude-code-start-here.md)：Claude Code 或 Codex 接手时从哪开始。
- [ARCHITECTURE.md](ARCHITECTURE.md)：数据怎么流动、各模块负责什么、哪些规则不能破坏。
- [AGENTS.md](AGENTS.md)：在这个仓库里工作的约定，包括 GitHub 账号检查。
- [每日总结说明](docs/DAILY_UPGRADE.md)、[功能日报说明](docs/FUNCTIONAL_DAILY.md)、[文档交接说明](docs/DOCUMENT_HANDOFF.md)、[运行切换说明](docs/RUNTIME_0.2.md)。

## 许可证

MIT（写在 `pyproject.toml` 里，仓库里还没有单独的 LICENSE 文件）。
