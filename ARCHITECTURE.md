# 架构与维护样板

## 数据流

```text
config.json → scope（登记、符号链接、有限 Git 指针检查）
            → collect.scan_project → git_readonly.collect_snapshot
            → store（不可变观察、scan_runs、绑定 run_id 的范围证明事件）
            → render.authorized_rows → reports（规则正文 + 来源校验文件）
            → handoff_view.inspect_handoff → CLI / SwiftBar

briefing_docs_root/{project_id}/00-项目说明.md
            → briefing.ingest_project（离线切块，持扫描锁）
            → reports/briefing/{id}.json
            → summarize extra（不可信静态理解；HEAD/测试/部署仍只来自 SQLite）
```

CLI 负责选择 home、锁、退出码和是否显式请求模型。Git 采集只执行只读命令，不 fetch、不运行项目测试、不执行 hooks。路径过滤与密钥片段规则在 `policy.py`。

## 目录职责

| 路径 | 职责 |
| --- | --- |
| `src/xiaomao/config.py`, `scope.py` | 登记与读取边界；缺省清单不等于自动发现授权 |
| `collect.py`, `git_readonly.py` | 项目扫描、状态指纹、历史状态重现、每次扫描实际覆盖证明；`branch_prefixes` 补充观察同仓其他工作树（不进范围证明） |
| `store.py` | SQLite schema、观察 / 证据 / 事件；同秒观察按插入顺序确定最新 |
| `render.py`, `reports.py` | 核对不可变来源后才加载事实，当前启用范围驱动正文 |
| `handoff_view.py` | 交接文件哈希、范围、观察 ID、扫描覆盖、时间和最新失败校验 |
| `report_access.py` | 旧日报 / 状态文件不能绕过当前授权范围 |
| `swiftbar.py`, `scripts/read-handoff.py` | 菜单与固定只读查询入口；参数作为数据传递 |
| `briefing.py` | 只读授权项目的 `00-项目说明.md`，切块消化为压缩 JSON；不是工作树扫描 |
| `menu_briefing.py` | 菜单短句；扫描 / 查询路径不加载模型 |
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

- 分支前缀树是补充观察：id 由真实路径哈希得出，`notes=branch_prefix`；不进入 `scope_identity`、`scan_runs` 结果和交接覆盖。消失记 `worktree_gone`，不记 error。
- 观察 A → B → A 要记录 A 再次出现，不得因指纹历史唯一约束误复用 B；下一次 A 再扫描才幂等。旧行不覆盖。
- 只读查询不运行 schema 迁移或模型；SQLite 的协调副文件与应用记录写入区分。
- 默认无外发通道。模型只得到经过范围过滤的事实，没有工具权限。
- 业务实现、当前测试、正式环境、安装版本和多日 Pilot 是不同证据。

## 日报窗口管线

    raw config → inventory.effective_config（身份元数据、候选与动态工作树）
               → collect / change_evidence（只读采集、内容指纹、脱敏差异）
               → activity（基线、样本、完整提交、逐树覆盖记录）
               → daily.build_bundle（固定窗口、仓库/SHA 去重、遗留 dirty）
               → daily_jobs（释放采集锁和事务后逐项目解读）
               → daily.render_bundle / menu_briefing.write_daily_briefing
               → 原子报告对、daily_windows、每窗口通知记账

日报和交接分别保留覆盖定义。动态树补充日报，不改变已登记交接的范围证明。日报范围哈希包含目录、所有者、工作树策略及时间设置；目录变化不会授权旧范围正文。身份元数据必须在内容读取前检查，动态路径若被替换则拒绝读取。

schema 3 只增量建表。daily_trees 保存当前前沿；daily_samples 保留不可变版本；daily_commits 按仓库和完整 SHA 保存有界证据，daily_commit_links 记录发现来源和窗口归属；daily_checks 记录扫描结果与缺口。首次接入不遍历并计入旧提交。

日报取窗口内的证据以及窗口之前的最后状态作比较。同一 dirty 文件的再次修改能被指纹区分；未变化的遗留状态不产生今日事件。修改到提交只有内容匹配才关联，clean/HEAD 变化本身不证明任务完成。迟到提交可修订旧窗口，未采集的未提交中间版本不推断恢复。

模型只获取同一包内的项目证据，不重读源码/项目说明。每项目校验引用和输出，错误降级；缓存键含模型、提示版本、范围、背景版本和项目证据。生成时持 daily.lock 防止报告互相覆盖，但不持 xiaomao.lock 或数据库事务。通知先记账再尝试发送；失败不自动重复。

## 功能理解与呈现

    change_evidence → feature_evidence（完整脱敏后切差异单元、函数附近上下文、小文件快照）
    activity → feature_context（当时 README、已有 briefing、用户目录修正）
             → daily.build_bundle（冻结对应窗口的背景版本与观察间差异）
             → feature_daily（功能归并、结构化模型协议、引用/方向/类型校验、逐项降级）
             → feature_render / menu_briefing（共享功能记录）
             → daily + daily-evidence + briefing（范围、正文哈希、证据及解读版本一致性）

schema 4 新增 feature_catalogs，只追加用户修正版本。项目说明和功能目录只能支持背景及归属；实现判断必须来自窗口内代码单元。归并先用目录中的符号和路径规则，再允许模型对未命名单元组织功能解释。同功能多版本保留引用链；有相反工作树状态时分开，能够确认整条提交链内容恢复时说明恢复。

小文件（每份最多 4096 字符）用保存的前后内容重新计算观察间差异，避免把昨日 dirty 全部写成今日新增。缺少快照时只能保留窗口内观察和 HEAD 差异，不推断完整增量；模型拒绝用此类不足片段确认行为。模型原文支持必须来自差异中对应的增加/移除方向，不能仅引用静态文档、旧版本、文件名或 SHA。程序派生作者、提交状态、进行中和覆盖情况，模型不能提升测试/发布状态。结构校验不等同于语义正确性证明，真实模型质量与运行验收另列。

## 显式文档交接

    CLI docs / optional MCP → DocumentStore → documents + document_revisions
                            → read / history / list（当前范围校验、只读连接）
                            → export（全部历史、固定版本 Markdown）

`document_commands.py` 处理文件或标准输入；MCP 只接收提交文本，启动时绑定 home、来源标签和可选项目范围。`documents.py` 负责脱敏、有界输入、不可变版本、请求去重、并发版本校验和导出。`document_mcp.py` 懒加载可选 SDK，普通扫描和 CLI 不依赖它。

schema 5 只增量增加两张表。保存与追加在短事务中完成，成功提交后返回回执。源材料不作为指令执行，来源自述不提升为已验证测试/部署状态。保存不触发推理或仓库扫描，读取不触发迁移。项目根目录身份改变或授权撤销后，旧内容保持保留并拒绝提供读取入口。文档交接不改变原有 Git 交接的覆盖与新鲜度定义。
