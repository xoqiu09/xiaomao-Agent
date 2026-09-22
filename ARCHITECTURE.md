# 架构与维护样板

## 数据流

```text
config.json → scope（登记、符号链接、有限 Git 指针检查）
            → collect.scan_project → git_readonly.collect_snapshot
            → store（不可变观察、scan_runs、绑定 run_id 的范围证明事件）
            → render.authorized_rows → reports（规则正文 + 来源校验文件）
            → handoff_view.inspect_handoff → CLI / SwiftBar
```

CLI 负责选择 home、锁、退出码和是否显式请求模型。Git 采集只执行只读命令，不 fetch、不运行项目测试、不执行 hooks。路径过滤与密钥片段规则在 `policy.py`。

## 目录职责

| 路径 | 职责 |
| --- | --- |
| `src/xiaomao/config.py`, `scope.py` | 登记与读取边界；缺省清单不等于自动发现授权 |
| `collect.py`, `git_readonly.py` | 项目扫描、状态指纹、历史状态重现、每次扫描实际覆盖证明 |
| `store.py` | SQLite schema、观察 / 证据 / 事件；同秒观察按插入顺序确定最新 |
| `render.py`, `reports.py` | 核对不可变来源后才加载事实，当前启用范围驱动正文 |
| `handoff_view.py` | 交接文件哈希、范围、观察 ID、扫描覆盖、时间和最新失败校验 |
| `report_access.py` | 旧日报 / 状态文件不能绕过当前授权范围 |
| `swiftbar.py`, `scripts/read-handoff.py` | 菜单与固定只读查询入口；参数作为数据传递 |
| `summarize.py`, `ollama_runtime.py`, `ops.py` | 可选本地解读、校验、去重、降级、卸载 |
| `schedule.py`, `scripts/xiaomao-*.sh` | 用户级调度；脚本从自身位置找源码 |
| `tests/`, `scripts/accept.py` | 临时 home 回归与明确隔离范围的验收 |

## 黄金样板：生成并查看交接

1. `cli.cmd_scan` → `collect.scan_project`：先拒绝越界项目，采集启用树；任一错误不能变成全项目成功。
2. `_record_scan_run`：扫描行和事件共用 run_id；事件记录采集开始时 scope hash 和每棵树的 observation ID。未覆盖的树不能借用最新扫描时间。
3. `cli.cmd_handoff` → `reports.write_handoff`：持扫描锁，`authorized_rows` 先查询不可变 `facts_json.toplevel` 元数据，确认与当前路径一致后才加载正文事实。`init` 修改登记不能给旧观察换来源。
4. 正文与 JSON 分别原子替换。读到不完整或不匹配文件对时返回未核实，不回退旧成功。
5. `inspect_handoff`：检查当前配置、最新准确项目文件、哈希、来源、覆盖、当前观察是否变化、最后扫描是否失败，以及报告引用的截止时间。文件名和 mtime 不作为新鲜证据。
6. `cmd_latest` / `render_menu`：共用上述结果，保留测试与部署 unknown，不触发采集或模型。

## 需要保留的不变量

- 观察 A → B → A 要记录 A 再次出现，不得因指纹历史唯一约束误复用 B；下一次 A 再扫描才幂等。旧行不覆盖。
- 只读查询不运行 schema 迁移或模型；SQLite 的协调副文件与应用记录写入区分。
- 默认无外发通道。模型只得到经过范围过滤的事实，没有工具权限。
- 业务实现、当前测试、正式环境、安装版本和多日 Pilot 是不同证据。
