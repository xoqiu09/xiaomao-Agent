# 小猫 / xiaomao-agent

本机只读的个人项目进度秘书：采集已登记个人仓库的 Git 磁盘状态，生成每日跨仓库日报和项目交接，并通过 CLI / SwiftBar 菜单展示，同时标出资料来源、时间和未核实项。采集与推理解耦：扫描和查询不加载模型；可选的本地模型只接收程序筛选过的事实，没有 shell、Git 或数据库写入工具。

## 功能

- 只读采集：`scan` 对已授权且启用扫描的工作树执行只读 Git 命令，不 fetch、不运行项目测试、不执行 hooks。可选按分支前缀或 `worktree_policy` 纳入同仓其他工作树。
- 每日跨仓库日报：默认 Asia/Taipei 前一日 21:30 至当日 21:30 的窗口，按功能归并变化；支持多工作树与副本去重、错过调度后的补报和一次 macOS 提醒。
- 项目交接：`handoff` 生成交接，`latest handoff` 读取时校验正文哈希、范围、观察 ID 和时效，未核实时不给快捷入口。
- 功能日报：按「具体功能 → 之前的限制 → 现在的变化 → 使用影响 → 交付状态」解读，模型失败或输出不受证据支持时保留规则结果。
- 项目说明消化：`ingest-briefing` 把授权项目的 `00-项目说明.md` 压缩成背景 JSON。
- 文档交接：`xiaomao docs` 保存明确提交的文档、追加带版本的阶段交接、导出 Markdown；可选 MCP 入口让 Codex 与 Claude Code 共用同一份本地文档库。
- SwiftBar 菜单：`scripts/swiftbar/xiaomao.1m.sh` 每分钟只读刷新，不采集、不加载模型。
- 调度：用户级 LaunchAgent（扫描 300 秒一次，日报 21:30），支持 `pause` / `resume` 暂停推理或扫描。

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

- 包版本 `0.2.2`。近期合入 main 的有：按功能解读的日报、窗口化跨仓库日报、带版本的跨 Agent 文档交接、分支前缀工作树观察、Pilot 归档、GitHub 写操作前的账号核对。
- 第一轮 Pilot（2026-09-21 起）已于 2026-09-30 `PILOT_ARCHIVED`：只观察了各仓主树，实际开发多在其他工作树，无法判断稳定性；不是 `STABILITY_PASSED`。
- 正式安装使用固定 release checkout，本仓 main 的改动不会自动影响正式运行；升级须先在新 checkout 跑隔离验收，再切换 plist 与插件链接。[EXECUTION_STATE.md](EXECUTION_STATE.md) 中的运行记录部分早于 0.2.2，实际运行状态需重新核对。
- 路线（2026-10-14 确认的[核心方案](docs/coordination/xiaomao-core-plan.md)）：阶段 1「看得懂、准时」：修复 `database is locked`、四档代码状态（未提交 / 仅本机 / 已推送 / 已合并）、白话晚报、规则版先发模型后补、通知带内容；阶段 2 双机镜像与主动提醒；阶段 3 项目进度时间线。以上均为计划，尚未实现。

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
