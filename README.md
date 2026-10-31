# 小猫 / xiaomao-agent

你的个人项目进度秘书。小猫每天晚上主动告诉你：今天哪些项目有进展、每项代码在哪（未提交 / 仅本机 / 已推送 / 已合并）、明天从哪继续。它只读取已登记个人仓库的 Git 磁盘状态，不替你写代码，也不派活。在 Session Steward 小镇里，它同样只做进度秘书，见[核心方案第十节](docs/coordination/xiaomao-core-plan.md)。

采集与推理分开：扫描和查询不加载模型；可选的本地模型只接收程序筛选过的事实，没有 shell、Git 或数据库写入工具。

## 核心功能

- **白话晚报**：每天 Asia/Taipei 21:30 结束当天窗口后生成 `reports/daily/<日期>.txt`。第一行一句话总结；最多 3 个项目、每个最多 3 条；最后是「明天从这里继续」（规则生成，最多 3 条）。正文不超过 40 行，不出现内部术语。
- **四档状态**：每条变化都标出 🟡 未提交、🟠 仅本机、🔵 已推送、🟢 已合并之一（`delivery_state.py`）。只读本机的 `refs/remotes`，不 fetch；没有远端的项目只显示前两档，并标「（无远端）」。
- **规则版先发，模型后补**：先发布规则版晚报并发一次通知，再调用本地模型；解读通过校验后原地更新同一份晚报，标题加「已补充解读」，不重复通知。模型不可用时规则版照常送达。
- **通知带内容**：macOS 通知的副标题是晚报第一行，正文是「明天先：」加第一条建议。
- **菜单栏**：SwiftBar 菜单「今天」一栏与晚报同一套白话：总结、每个项目一行、「明天先」；「很久没看」列出 14 天以上没提交的项目。每分钟只读刷新，不采集、不加载模型。
- **依据文件**：完整的功能解读、代码位置、四档判定依据和采集问题放在 `reports/daily-evidence/<日期>.txt`，从菜单「查看日报依据」打开。白话正文只是另一种呈现，证据没有减少。

## 其他功能

- 只读采集：`scan` 对已授权且启用扫描的工作树执行只读 Git 命令，不 fetch、不运行项目测试、不执行 hooks。可选按分支前缀或 `worktree_policy` 纳入同仓其他工作树。扫描在数据库事务外读取 Git，不再长时间占用写锁。
- 窗口化日报：默认前一日 21:30 至当日 21:30，按功能归并变化；支持多工作树与副本去重、错过调度后的补报，每个窗口只通知一次。
- 项目交接：`handoff` 生成交接，`latest handoff` 读取时校验正文哈希、范围和时效。
- 项目说明消化：`ingest-briefing` 把授权项目的 `00-项目说明.md` 压缩成背景 JSON。
- 文档交接：`xiaomao docs` 保存明确提交的文档、追加带版本的阶段交接、导出 Markdown；可选 MCP 入口让 Codex 与 Claude Code 共用同一份本地文档库。
- 调度：用户级 LaunchAgent（扫描 300 秒一次，日报 21:30，另每 300 秒检查错过的窗口），支持 `pause` / `resume` 暂停推理或扫描。

## 技术栈

- Python 3.11+，核心无第三方依赖；构建后端 `hatchling`
- SQLite（标准库 `sqlite3`）
- 可选：`mcp>=2.2,<3`（文档交接 MCP 服务，extras `mcp`）
- 可选：本地 Ollama 模型（默认 `qwen3-coder:30b`，`keep_alive=0`，不切云端、不下载模型）
- macOS：launchd LaunchAgent、SwiftBar 插件
- 测试：`unittest`（`tests/`），`pyproject.toml` 也配置了 pytest

## 快速开始

### 环境要求

- macOS
- Python 3.11 及以上（`python3.11`）
- Git
- 可选：SwiftBar（`scripts/install-swiftbar.sh` 可安装并链接插件）、Ollama

### 安装

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install --no-deps --editable .
# 需要文档交接 MCP 服务时：
.venv/bin/python -m pip install --editable '.[mcp]'
```

不安装也可以直接从源码运行：

```bash
export PYTHONPATH="$PWD/src"
python3.11 -m xiaomao --help
```

### 配置

- 正式数据目录为 `~/Library/Application Support/Xiaomao/`，全局参数 `--home PATH` 可覆盖；试用和测试请使用临时目录。
- 环境变量：`XIAOMAO_HOME`（数据目录）、`XIAOMAO_PYTHON`（调度脚本使用的 Python）、`TZ`。
- 数据目录内：`config.json`（项目登记）、`xiaomao.sqlite`、报告目录 `reports/daily/`、`reports/handoff/`、`reports/projects/`、`reports/briefing/`。
- 默认只保留既有六项登记，旧配置不会自动扩大范围，`projects: []` 也不会扩回默认清单。显式配置 `personal_roots` 后才识别已确认的个人仓库；未知来源只列为候选。公司项目、退役路径、归档目录和第三方工具不进入活动采集。
- 项目可选字段：`branch_prefixes`（如 `["codex/"]`，补充观察同仓前缀分支的工作树，不进入范围证明）、`menu_hide_dirty`（只隐藏菜单里的未提交清单）。

```bash
xiaomao --home /tmp/xiaomao-try init
xiaomao --home /tmp/xiaomao-try doctor
xiaomao projects discover --root /path/to/personal-projects
xiaomao projects configure --project PROJECT_ID --worktrees all
xiaomao projects configure --time 21:30 --timezone Asia/Taipei --notify on
```

窗口、覆盖、预算和迁移细节见 [每日总结升级说明](docs/DAILY_UPGRADE.md)。

### 运行

```bash
# 采集与生成（两步都不调用模型）
xiaomao scan --project PROJECT_ID
xiaomao handoff --project PROJECT_ID
xiaomao latest handoff --project PROJECT_ID --print

# 日报
xiaomao daily --date 2026-10-02
xiaomao daily --with-model
xiaomao latest daily --print
xiaomao latest daily --details --print
xiaomao projects coverage --date 2026-10-02

# 功能目录、调度与运行状态
xiaomao features show --project PROJECT_ID
xiaomao health
xiaomao pause scan      # 对应 resume scan；infer 同理
xiaomao pilot status
```

`latest handoff` 返回码：`0` 资料可读且在 11 分钟时效内（不代表测试通过或已上线）；`2` 没有交接文件；`3` 陈旧、来源或范围变化、旧格式缺少校验、采集失败等未核实状态。`open handoff --project ID` 同样先校验。测试与部署栏始终为 `unknown`，本程序不执行业务仓测试，也没有远程连接器。

`--date` 表示该日截止的窗口，提前生成标为预览。首次观察只建基线；没有当日新变化的遗留修改只列为仍在进行。

文档交接：

```bash
xiaomao --home /absolute/path/xiaomao-documents docs connection --client claude-code
xiaomao --home /absolute/path/xiaomao-documents docs template
```

完整用法见 [文档交接说明](docs/DOCUMENT_HANDOFF.md)。

### 测试

```bash
PYTHONPATH=src python3.11 -m unittest discover -s tests
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3.11 scripts/accept.py --isolated
```

`accept.py --isolated` 使用临时 HOME、临时数据目录和真实临时 Git worktree，包含全套 unittest、两次扫描幂等、业务文件 / index / hooks 不变、日报、交接查询和本仓 worktree 身份。退出 `0` 仅表示隔离门通过；正式 home 与外盘身份两门为 `NOT_RUN`，`full_live_acceptance=false`。

## 目录结构

```text
src/xiaomao/            核心包：cli.py 入口，采集、存储、日报、交接、文档、SwiftBar 渲染
tests/                  unittest 回归（临时 home 与合成 Git 仓）
scripts/                accept.py 隔离验收、调度脚本 xiaomao-scan.sh / xiaomao-daily.sh、Ollama 启停
scripts/swiftbar/       SwiftBar 插件 xiaomao.1m.sh 与图标
integrations/skills/    xiaomao-handoff 技能（Agent 调用文档交接）
docs/                   日报、功能日报、文档交接、0.2 运行切换说明
docs/coordination/      核心方案、接手说明与重设计参考
```

调用链与各模块职责见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 当前状态与路线

- **阶段 1「看得懂、准时」已完成**（PR #10、#11）：修复 `database is locked`、四档状态、白话晚报、规则版先发模型后补、通知带内容、白话菜单。正式安装自 2026-10-28 起运行 `release/main-ec61e21`。`pyproject.toml` 的包版本号仍为 `0.2.2`，没有随之更新。
- **已知问题**（2026-10-31 核对，详见[核心方案第七节](docs/coordination/xiaomao-core-plan.md)）：GitHub 压缩合并后原提交仍显示「已推送」；很久以前的本机分支每天都被提醒推送；晚报偶尔晚于 21:45；后台偶发读取主仓库失败；本机模型不可用时条目直接显示提交标题。
- **路线**（[核心方案](docs/coordination/xiaomao-core-plan.md)）：先修上述已知问题；再做阶段 2（两台机器 + 开工提醒、未推送提醒、继续卡片），然后是阶段 3（功能时间线与每周进度）。接入小镇后，PR 合并状态和另一台机器的状态改从小镇的只读事实接口读取。
- 正式安装使用固定 release checkout，本仓 main 的改动不会自动影响正式运行；升级须先在新 checkout 跑隔离验收、备份正式状态，再切换 plist 与插件链接。[EXECUTION_STATE.md](EXECUTION_STATE.md) 中的运行记录早于当前版本，实际运行状态需重新核对。
- 第一轮 Pilot（2026-09-21 起）已于 2026-09-30 `PILOT_ARCHIVED`，不是 `STABILITY_PASSED`。

## 相关文档

- [ARCHITECTURE.md](ARCHITECTURE.md)：数据流、目录职责与不变量
- [CONTRIBUTING.md](CONTRIBUTING.md)：修改与验证步骤
- [EXECUTION_STATE.md](EXECUTION_STATE.md)：运行身份与验收记录
- [AGENTS.md](AGENTS.md)：优化工作约定
- [docs/DAILY_UPGRADE.md](docs/DAILY_UPGRADE.md)：每日跨仓库总结
- [docs/FUNCTIONAL_DAILY.md](docs/FUNCTIONAL_DAILY.md)：功能日报
- [docs/DOCUMENT_HANDOFF.md](docs/DOCUMENT_HANDOFF.md)：文档交接与 MCP
- [docs/RUNTIME_0.2.md](docs/RUNTIME_0.2.md)：0.2.2 运行切换、发布与恢复
- [docs/coordination/README.md](docs/coordination/README.md)：审阅与接手入口

## 许可证

MIT（见 `pyproject.toml`，仓库内暂无单独的 LICENSE 文件）。
