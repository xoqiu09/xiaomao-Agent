# 小猫 / xiaomao-agent

本机只读工程观察员：采集已登记个人项目的 Git 磁盘状态，生成日报与交接，并在 CLI / SwiftBar 里显示资料来源、时间和未核实项。采集不加载模型；模型只接收程序筛选的事实，没有 shell、Git 或数据库写入工具。

## 随时保存文档与交接

`xiaomao docs` 可以保存明确提交的 Markdown / 文本文档，按关键词找回，追加带来源和版本的阶段说明，并导出包含原文与全部交接的 Markdown。保存不调用模型，旧版本保留。

可选 MCP 入口让 Codex、Claude Code 共用同一份本地文档库；附两端接入配置生成命令和 `xiaomao-handoff` 调用技能。安装、使用与验证边界见 [文档交接说明](docs/DOCUMENT_HANDOFF.md)。

## 每日跨仓库总结

默认汇总 **Asia/Taipei 前一日 21:30 至当日 21:30**，正文为「今日总览 → 各项目变化 → 尚未提交的工作 → 采集缺口」。支持提交后 clean、连续编辑同一文件、多工作树及副本去重、逐项目本地解读和错过调度后的补报。

    python3.11 -m xiaomao daily --date 2026-10-02
    python3.11 -m xiaomao latest daily --print
    python3.11 -m xiaomao projects discover --root /Users/xiuqiu/PersonalProjects
    python3.11 -m xiaomao projects coverage --date 2026-10-02

--date 表示该日截止的窗口，提前生成标为预览。首次观察只建基线；没有当日新变化的遗留修改只列为仍在进行。配置、预算、提醒、迁移和发布方式见 [每日总结升级说明](docs/DAILY_UPGRADE.md)。

## 查看最新交接

在本仓根目录运行（Python 3.11+，核心无第三方依赖）：

```bash
export PYTHONPATH="$PWD/src"
python3.11 -m xiaomao latest handoff --project xiaomao-Agent --print
```

这条查询读取已有报告和所需的 SQLite 元数据，不扫描项目、不加载模型。它会显示生成时间、采集核对截止时间、观察 ID，以及测试 / 部署仍为 `unknown`。有多个项目时必须用 `--project` 选择；只有一个可读取项目时可省略。

- 返回 `0`：资料可读且仍在 11 分钟时效内；不代表测试通过或已经上线。
- 返回 `2`：没有交接文件。
- 返回 `3`：陈旧、来源或范围变化、旧格式缺少校验、采集失败 / 缺失等未核实状态。
- `open handoff --project ID` 也先校验；未核实时不会直接打开旧文件。

需要更新资料时，明确执行采集和生成。两步都不调用模型：

```bash
python3.11 -m xiaomao scan --project xiaomao-Agent
python3.11 -m xiaomao handoff --project xiaomao-Agent
python3.11 -m xiaomao latest handoff --project xiaomao-Agent --print
```

报告 `.txt` 与同名 `.json` 保存正文哈希、生成时间、扫描范围和观察 ID。只改文件 mtime 不会刷新资料。重新启用或改指工作树后要重新扫描；单棵树的成功时间不能替另一棵树证明新鲜。旧报告和历史观察保留；旧格式先标未核实，不自动升级为通过。

## 范围与数据位置

正式数据在 `~/Library/Application Support/Xiaomao/`；全局 `--home PATH` 可覆盖。配置文件是 `config.json`，SQLite 为 `xiaomao.sqlite`，报告在 `reports/daily/`、`reports/handoff/`、`reports/projects/`、`reports/briefing/`。

默认只保留既有六项登记：QAI、wallet-core、xiaomao-Agent、xiuqiu-site、AI-Web3-Learning、Wallet-Infrastructure。旧配置不会自动扩大范围；显式配置 personal_roots 后识别已确认个人仓库，worktree_policy=all 可纳入同仓普通分支和 detached 开发树。未知来源只列候选。`projects: []` 不会扩回默认清单。

可选的分支前缀观察：在项目里加 `"branch_prefixes": ["codex/"]` 后，扫描主树时会读取同一仓库的 `git worktree list`，分支名以这些前缀开头的其他工作树会被一并只读采集。约束如下：
- detached、bare、目录已删除（prunable）的树不纳入；路径命中排除规则、或已不属于同一仓库的树不采集。
- 这些树不写回 `config.json`，不计入交接的范围哈希、交接覆盖和 `scan_runs` 结果；它们出错也不会把项目扫描判为失败。
- 之前纳入的树从列表里消失后，记一条 `worktree_gone` 事件并停止观察，不算错误。
- 日报的「查看依据」列出其他工作树的分支、HEAD、观察时间和未提交状态。正文按功能归并；菜单的技术详情仍可按树查看。
默认为空，即不观察其他树。

公司项目（包括 theAIapp-service 各树、event-services-chooseme-event）、已登记的退役路径、`_待删除旧项目_2026-09-22` 归档以及 Stats / AgentNotch / TokenMonitor 第三方工具不进入活动采集。兼容链接和 Git 的 `.git` / `commondir` 指针也受检查；只检查有限元数据，命中排除目标即停止。完全改名且没有可识别来源指针的独立克隆仍需要维护者明确识别并排除。

历史日报可能包含后来撤销的项目。`latest` / `open` 只接受与当前范围匹配、正文哈希有效的日报 / 状态文件；旧文件保留但不提供绕过校验的快捷入口。

## SwiftBar

插件为 `scripts/swiftbar/xiaomao.1m.sh`，每分钟刷新。刷新本身不采集、不加载模型。菜单栏没事时只有小八的猫头（`image=` 彩色 PNG）；扫描过期显示「信息已过期」，最近一次扫描失败显示「扫描失败」。缺少或落后的交接不改标题——那不是扫描故障。

菜单优先显示标明窗口及覆盖情况的功能短句，与完整正文使用同一份证据包及功能记录。「查看日报依据」打开提交、工作树、代码片段及功能解读的原文支持。菜单、正文、依据版本不一致时不提供已核实入口。没有有效日报时保留原有模块与提交时间显示；新日报不重新判断历史活跃度。

菜单其余分层：

- 主区：今日日报入口。日报必须通过范围校验才可点开；未核实只说明原因，不给快捷入口。
- 「需要处理」：状态库不可读、陈旧、扫描失败 / 跳过、扫描暂停、某棵树采集 error。
- 折叠的「技术细节」：「工作区未提交」按树列计数和最多两个文件名（只取已存事实里的 basename，不打开业务文件）；「交接 / 未核实项」只为已有交接文件的项目给“查看交接与未核实项”按钮，在终端执行同一只读 CLI 并重新校验。

长期不动的旧仓可以在 `config.json` 里给该项目加 `"menu_hide_dirty": true`：它仍被扫描、仍写进日报，只是未提交清单不再出现在菜单里；它的采集 error 依旧进「需要处理」。默认 `false`，只影响菜单显示，不改授权范围。

插件解析自己的真实路径，因此可以从隔离源码树运行。现用安装及源码身份见 [EXECUTION_STATE.md](EXECUTION_STATE.md)。已有 SwiftBar 不需要重新安装应用；切换插件软链接和调度脚本前先备份当前指向与 plist。

SQLite 使用 `mode=ro` / `query_only` 读取，不修改应用记录或报告。SQLite 可能创建自己的 WAL / SHM 协调文件；这不等于数据目录零文件变化。

## 调度、模型与 Pilot

- `ai.xiaomao.scan`：300 秒一次，扫描当前登记范围，完全不加载模型；任何树失败、排除、暂停或没有实际结果，CLI 都不报整体成功。
- `ai.xiaomao.daily`：按 Asia/Taipei 21:30 窗口运行 `daily --scheduled`，候选调度描述增加启动及每 300 秒遗漏检查，每次补最多 7 个窗口。有变化的项目分别调用已有本地模型；同证据复用摘要，失败保留规则日报。模型在采集事务和锁之外运行。每个窗口只尝试一次 macOS 提醒。
- 项目说明消化：`ingest-briefing`（可加 `--project ID`）。只读已授权项目在 `briefing_docs_root` 下的 `00-项目说明.md`，切块后用深度模型压成 `reports/briefing/{id}.json`。总doc 本身不是工作树；扫描可校验并复用该压缩背景和有界 README，不调用模型。SwiftBar / `latest` 只读保存的结果。`00` 未改则跳过消化。
- 手动模型解读：`daily --with-model` 或 `handoff --project ID --with-model`。默认深度 `qwen3-coder:30b`，`keep_alive=0`，失败降级；不切云端、不下载模型。日报使用经过脱敏的窗口证据，交接可继续使用压缩项目说明。测试与部署仍须独立证据。
- `pause infer` / `resume infer`：控制推理；`pause scan` / `resume scan`：控制本系统任务。Pilot 起算和历史记录保留。
- `pilot start` / `pilot status` / `pilot archive --reason TEXT`：归档结束当前一轮，不给通过或不通过结论；本轮元数据和窗口计数写入 `pilot_archived` 事件，扫描历史不动。归档后再 `start` 从当时重新起算。

第一轮 Pilot（2026-09-21 起）已于 2026-09-30 **PILOT_ARCHIVED**：只观察各仓主树，实际开发多在其他工作树，无法判断稳定性。不是 STABILITY_PASSED。CLI 回归、菜单运行、一次真实扫描都不能代替多日稳定性证据。

## 按功能阅读日报

正文固定为「今日总览 → 各项目功能变化 → 仍在推进的功能 → 待确认与采集缺口」。同一功能涉及多文件或提交时合并呈现，一笔提交涉及不同功能时拆开；每项目优先显示最多 5 项，完整条目保存在依据中。已提交和正式启用分别判断，作者来自 Git 证据。

```bash
xiaomao daily --with-model
xiaomao latest daily --print
xiaomao latest daily --details --print
xiaomao open daily --details
xiaomao features show --project PROJECT_ID
```

功能解释使用当时保存的项目背景与代码片段。缺少上下文、模型不可用或输出不受证据支持时，正文明确写「功能影响待确认」。规则摘要仍保留功能归属、提交状态和全部依据。功能名称可通过 `features set --project PROJECT_ID --file catalog.json` 修正，从后续采集生效；格式、限制和语义验收见 [功能日报说明](docs/FUNCTIONAL_DAILY.md)。

## 开发与验收

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install --no-deps --editable .
.venv/bin/python -m unittest discover -s tests
.venv/bin/python scripts/accept.py --isolated
```

该入口默认就使用临时 HOME、临时数据目录和真实临时 Git worktree；包含全套 unittest、两次扫描幂等、业务文件 / index / hooks 不变、日报、交接查询和本仓 worktree 身份。退出 `0` 仅表示六个隔离门通过；正式 home 与外盘身份两门明确 `NOT_RUN`，`full_live_acceptance=false`。

目录与真实调用链见 [ARCHITECTURE.md](ARCHITECTURE.md)，修改及验证步骤见 [CONTRIBUTING.md](CONTRIBUTING.md)。
