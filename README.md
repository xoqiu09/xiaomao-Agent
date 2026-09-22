# 小猫 / xiaomao-agent

本机只读工程观察员：采集已登记个人项目的 Git 磁盘状态，生成日报与交接，并在 CLI / SwiftBar 里显示资料来源、时间和未核实项。采集不加载模型；模型只接收程序筛选的事实，没有 shell、Git 或数据库写入工具。

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

正式数据在 `~/Library/Application Support/Xiaomao/`；全局 `--home PATH` 可覆盖。配置文件是 `config.json`，SQLite 为 `xiaomao.sqlite`，报告在 `reports/daily/`、`reports/handoff/`、`reports/projects/`。

默认只保留既有六项登记：QAI、wallet-core、xiaomao-Agent、xiuqiu-site、AI-Web3-Learning、Wallet-Infrastructure。新树不会自动获得权限，`projects: []` 不会扩回默认清单。

公司项目（包括 theAIapp-service 各树、event-services-chooseme-event）、已登记的退役路径、`_待删除旧项目_2026-09-22` 归档以及 Stats / AgentNotch / TokenMonitor 第三方工具不进入活动采集。兼容链接和 Git 的 `.git` / `commondir` 指针也受检查；只检查有限元数据，命中排除目标即停止。完全改名且没有可识别来源指针的独立克隆仍需要维护者明确识别并排除。

历史日报可能包含后来撤销的项目。`latest` / `open` 只接受与当前范围匹配、正文哈希有效的日报 / 状态文件；旧文件保留但不提供绕过校验的快捷入口。

## SwiftBar

插件为 `scripts/swiftbar/xiaomao.1m.sh`，每分钟刷新。按当前可读取个人项目显示扫描与交接状态，提供“查看交接与未核实项”按钮，在终端执行同一只读 CLI。按钮重新校验资料，刷新本身不采集、不加载模型。

插件解析自己的真实路径，因此可以从隔离源码树运行。现用安装及源码身份见 [EXECUTION_STATE.md](EXECUTION_STATE.md)。已有 SwiftBar 不需要重新安装应用；切换插件软链接和调度脚本前先备份当前指向与 plist。

SQLite 使用 `mode=ro` / `query_only` 读取，不修改应用记录或报告。SQLite 可能创建自己的 WAL / SHM 协调文件；这不等于数据目录零文件变化。

## 调度、模型与 Pilot

- `ai.xiaomao.scan`：300 秒一次，扫描当前登记范围，完全不加载模型；任何树失败、排除、暂停或没有实际结果，CLI 都不报整体成功。
- `ai.xiaomao.daily`：本机时区 21:30，`daily --scheduled`，仅有合适的新证据时尝试本地解读。
- 手动模型解读：`daily --with-model` 或 `handoff --project ID --with-model`。默认深度 `qwen3-coder:30b`，`keep_alive=0`，失败降级；不切云端、不下载新模型。
- `pause infer` / `resume infer`：控制推理；`pause scan` / `resume scan`：控制本系统任务。Pilot 起算和历史记录保留。

状态仍是 **PILOT_RUNNING**，不是 STABILITY_PASSED。CLI 回归、菜单运行、一次真实扫描都不能代替多日稳定性证据。

## 开发与验收

```bash
PYTHONPATH=src python3.11 scripts/accept.py --isolated
```

该入口默认就使用临时 HOME、临时数据目录和真实临时 Git worktree；包含全套 unittest、两次扫描幂等、业务文件 / index / hooks 不变、日报、交接查询和本仓 worktree 身份。退出 `0` 仅表示六个隔离门通过；正式 home 与外盘身份两门明确 `NOT_RUN`，`full_live_acceptance=false`。

目录与真实调用链见 [ARCHITECTURE.md](ARCHITECTURE.md)，修改及验证步骤见 [CONTRIBUTING.md](CONTRIBUTING.md)。
