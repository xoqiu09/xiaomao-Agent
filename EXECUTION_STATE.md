# EXECUTION_STATE.md

更新时间：2026-09-21 09:00 CST
状态：**PILOT_RUNNING（单工作树试运行已启动；不是 STABILITY_PASSED）**

本文件是唯一交接状态记录。不要另开平行交接文档。

## 当前

在 v0.1 当前验收门之上交付可日常使用的单工作树试运行。默认深度仍是 **qwen3-coder:30b**。扫描用户级 LaunchAgent 未重装。新增独立日报 Agent `ai.xiaomao.daily`（本机时区 21:30）。KEEP_ALIVE=0 不改。扫描不加载模型。请求结束后 `/api/ps` 为空。

### 本轮提交与运行时

- 本仓提交：`06e8b7e2b937a54b360aed1f1ca4b5af23e4169a`
- 核对起点：`d1133bece1245b4246b37ac7ef58580919efc284`
- 程序版本：`xiaomao 0.1.0`
- 形式运行时：`~/Library/Application Support/Xiaomao/`
- schema：2（`scan_runs` / `events`；现有库下次 `open_db` 自动补表）
- 观察范围：仅 **website-main** 扫描。website-auth / website-integration 登记不扫。publish 未授权。test/deploy 无证据，保持 unknown。
- 业务仓 HEAD（只读核）：`1dae06ad7436`（`feat/website-backend-v0.1`）

### 调度

- `ai.xiaomao.scan`：plist mtime 2026-09-20 10:42，**未重写**。interval 300s。本轮核实 runs 从 60 → 267，last exit 0。
- `ai.xiaomao.daily`：`~/Library/LaunchAgents/ai.xiaomao.daily.plist`，CalendarInterval 21:30 Asia/Taipei。Program `scripts/xiaomao-daily.sh` → `daily --scheduled`。已 bootstrap，runs=0（等到今晚）。无新证据不加载 30b。

### 已核实命令与路径

```
export PYTHONPATH=/Users/xiuqiu/WorkSpace/xiaomao-Agent/src
export XIAOMAO_HOME="$HOME/Library/Application Support/Xiaomao"

python3.11 -m xiaomao --home "$XIAOMAO_HOME" health
python3.11 -m xiaomao --home "$XIAOMAO_HOME" status --project website
python3.11 -m xiaomao --home "$XIAOMAO_HOME" latest daily
python3.11 -m xiaomao --home "$XIAOMAO_HOME" latest handoff --project website
python3.11 -m xiaomao --home "$XIAOMAO_HOME" open daily
python3.11 -m xiaomao --home "$XIAOMAO_HOME" pause infer
python3.11 -m xiaomao --home "$XIAOMAO_HOME" resume infer
python3.11 -m xiaomao --home "$XIAOMAO_HOME" pause scan
python3.11 -m xiaomao --home "$XIAOMAO_HOME" resume scan
```

- 规则日报：`~/Library/Application Support/Xiaomao/reports/daily/2026-09-21.txt`（accept 于 09:02 覆盖为规则版，1723 bytes）
- 30b 日报副本：`~/Library/Application Support/Xiaomao/reports/daily/2026-09-21.model.txt`（2723 bytes，校验 pass，文案含「授权观察范围内无新变化」；accept 未删）
- 30b Handoff：`~/Library/Application Support/Xiaomao/reports/handoff/website-20260921-085854.txt`（校验 pass；第二次同快照未再加载模型）
- 规则 Handoff：`~/Library/Application Support/Xiaomao/reports/handoff/website-20260921-085655.txt`
- accept 规则 Handoff：`~/Library/Application Support/Xiaomao/reports/handoff/website-20260921-090251.txt`

### 模型 / 卸载

- 默认深度 qwen3-coder:30b。12b / 35b 保留，本轮未调用、未重测、未删除。
- 30b 原评测 3 条不可用均为 `unwarranted_completion`（结构 ok、非超时、非事实错误）。未放宽校验器。
- 一次 `--with-model` 日报：summary pass；prompt_eval_count=996 eval_count=409 load_duration≈60.7s eval_duration≈4.0s retry_count=0。
- 请求前 `/api/ps` = `{"models":[]}`；请求后 `/api/ps` = `{"models":[]}`；events.model_unload `loaded_after=[]`。KEEP_ALIVE=0 实际卸载。
- 锁冲突记 `scan_skip` / `lock_busy`，不崩。

### 试运行窗口

- 开始：2026-09-21T00:58:55+00:00（CST 08:58）
- 第 3 天核对：**2026-09-24**
- 第 7 天核对：**2026-09-28**
- 至少 3 个有真实开发输入的日期才够样本；否则保持 pending。
- 本会话不空转等 7 天。本地程序继续记 scan_runs / events。
- 「用户觉得有用」只来自真实反馈，不自动填写。

### 暂停 / 恢复

- 停推理：`xiaomao pause infer`（扫描继续）
- 停本系统定时任务：`xiaomao pause scan`（bootout scan+daily，不删 plist）
- 恢复：`xiaomao resume infer` / `xiaomao resume scan`
- 停 Ollama：`/bin/sh scripts/user_stop_ollama.sh`
- 不改整机睡眠策略，不 sudo。

### 未完成 / 不声称

- **不是 STABILITY_PASSED。** 时间门从本轮确认的运行时起算。
- 不接第二棵工作树、QAI、远程测试/部署。
- 不下载新模型。不重测 35b。
- 采集缺口（睡眠/关机/登出）会记 gap；缺口内未落盘编辑不可见，不编造。
- 存储预算 2GB；超预算限制新模型正文并告警，不自动删模型/历史，不碰业务文件。当前约 2.1MB。

## 复现验收

```
PYTHONPATH=src python3.11 scripts/accept.py
```

本轮 2026-09-21 09:02 CST：`ok=true`，8/8 全过（unittest / volume_match / formal_home / business_unchanged / scan_idempotent / daily / handoff / repo_git）。业务仓 HEAD 前后均为 `1dae06ad7436`，index mtime 未变。accept 当时本仓 HEAD 仍是 `d1133be`。accept 的 daily 门会写规则日报，30b 正文看 `.model.txt`。

## 恢复

- 停 ollama：`/bin/sh scripts/user_stop_ollama.sh`
- 停扫描：`xiaomao pause scan` 或 `launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/ai.xiaomao.scan.plist`
- 停日报：`launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/ai.xiaomao.daily.plist`
- 不关沙盒、不 sudo、不改全局 Git、不写业务仓
