# 小猫接手说明（Claude Code / Codex 通用）

更新：2026-10-14。本说明取代此前的「P0 + P1 项目接续」版本。

> **2026-10-31 状态**：下文的阶段 1 已全部完成并合入 main（PR #10、#11），正式安装运行 `release/main-ec61e21`。现在要做的是核心方案第七节列出的「实际使用发现的问题」，之后进入阶段 2。小猫在小镇里的定位见核心方案第十节。下文保留作阶段 1 的任务记录。

## 先读什么

1. [小猫核心方案](xiaomao-core-plan.md)：**主线**。它说明小猫要做什么、不做什么，以及分阶段的任务。
2. `AGENTS.md`：工作规则、源码身份、GitHub 账号检查。
3. `ARCHITECTURE.md`：数据流、模块职责、不变量。
4. `CONTRIBUTING.md`：修改与验证步骤。

`xiaomao-agent-redesign.md` 仅作参考。其中的任务系统、协调进程和自动派发**不实施**。

## 用户要什么（一句话）

每天主动告诉用户：做了什么、多了哪些核心功能、做到哪、代码在哪（未提交 / 仅本机 / 已推送 / 已合并）。开工时告诉用户从哪继续。报告要让用户**读得懂**。

## 当前要做：阶段 1「看得懂、准时」

从 `codex/xiaomao-optimization-20261013`（v0.2.2 基线 `adc2bf9`）或本说明所在分支开新分支。每一项单独提交。

### 1. 复现并修复 `database is locked`

- 现场：日报错误日志累计有 440 行 `database is locked`；10/13 窗口的日报到 10/14 12:06（台北）才完成。
- 排查方向（不要预设为根因）：
  - `store.open_db` 每次打开都执行 `init_db`，只读入口也会写。
  - `daily_jobs.run_daily` 中多次 `open_db`，并与 `ScanLock` 交错。
  - 300 秒扫描和日报的 300 秒补报检查可能并发。
- 做法：用临时 home 和合成 Git 仓，两个进程并发执行扫描和日报，先稳定复现，再做最小修复。在测试里固定复现条件。

### 2. 四档状态

- 在 `git_readonly.py` 增加只读查询：
  - `for-each-ref --format=%(refname) %(objectname) refs/remotes`
  - `merge-base --is-ancestor <sha> <ref>`
  - 默认分支：先读 `symbolic-ref refs/remotes/origin/HEAD`，失败则依次尝试 `origin/main`、`origin/master`。
- 新模块 `src/xiaomao/delivery_state.py`：输入工作树和提交 SHA 列表，输出每个 SHA 的状态（`uncommitted` / `local_only` / `pushed` / `merged`），以及项目有无 remote。
- 状态在**生成日报时计算**，并冻结进 bundle，不改历史观察表。
- 测试（`tests/test_delivery_state.py`）：用本地 bare 仓模拟 origin，覆盖四档各一例、无 remote、默认分支为 master、分支已删除但提交已合并。

### 3. 白话晚报

- 在 `feature_render.py` 新增 `render_plain_summary(bundle, notes)`，严格按核心方案第三节的写作规则和样例输出。`render_summary` 保留不删。
- 晚报文件 `reports/daily/<date>.txt` 改用白话版。依据文件 `daily-evidence` 不变。菜单「查看依据」的入口不变。
- `bind_report` 的哈希与范围校验照旧适用于新正文。
- 「明天从这里继续」由规则生成，候选来源：
  - 有仅本机的提交 → 推送
  - 有已推送但未合并的分支 → 合并
  - 未提交改动超过 20 处或超过 24 小时 → 分批提交
  - 按影响排序，最多 3 条。
- 测试：用 `tests/daily_scenario.py` 或 `feature_scenario.py` 构造与 10/13 同结构的数据。断言输出不超过 40 行、每条都带状态标签、不含禁用词表。禁用词表放在模块常量里，测试直接引用。

### 4. 规则版先发，模型后补

- 调整 `daily_jobs.run_daily`：采集 → `build_bundle` → 写规则版晚报 → `notify_once` → 再调用模型 → 原地更新晚报，并加一行「已补充解读」。
- 更新时不再发通知，`daily_notifications` 照旧保证每个窗口只发一次。
- 测试：模型客户端抛异常或超时时，规则版晚报和通知照常产生。

### 5. 通知带内容

- `macos_notification` 的正文改为晚报第一行总结，副标题为第一条「明天从这里继续」。参数仍以 argv 传入 AppleScript，不做字符串拼接。

## 验收与交付

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3.11 -m unittest discover -s tests
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3.11 scripts/accept.py --isolated
```

交付内容：
- 每项任务的提交
- 锁问题的复现证据
- 测试结果
- 一份用合成数据生成的白话晚报样例
- 未解决事项

正式 home、外盘、模型质量、通知是否真的被看到，这几项分开记录，未执行的写 `NOT_RUN`。

## 不要做

- 不做核心方案「暂不做」一节中的任何内容：任务系统、协调进程、自动派发、导出包。
- 不在业务仓中执行 fetch、写入或测试。阶段 2 的镜像只写小猫自己的数据目录。
- 不切换正式安装（plist、SwiftBar 链接、release 目录）。切换由用户确认后另行进行。
- 不读取公司项目、公司材料或来源不明的项目。

## 阶段 1 完成之后

用户实际使用一周后，再进入阶段 2（两台机器的镜像、早上提醒、未推送提醒、`xiaomao resume`）。详见核心方案第七节。
