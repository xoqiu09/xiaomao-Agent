# EXECUTION_STATE.md

更新时间：2026-09-21
状态：**PILOT_RUNNING（不是 STABILITY_PASSED）**

本文件是唯一交接状态记录。不要另开平行交接文档。

## 产品

Xiaomao 是部署在 Mac Studio 上、长期运行的本地私有工程观察 Agent。只读观察授权的本地开发活动。默认深度 **qwen3-coder:30b**。KEEP_ALIVE=0。扫描不加载模型。

## 运行时

- 程序版本：`xiaomao 0.1.0`
- 本仓 HEAD：`89fedd3dcb5a16c7ccf2764904dacdf81877d168`（2026-09-21 hygiene）
- 实现基线：`06e8b7e`（单工作树试运行）
- 形式运行时：`~/Library/Application Support/Xiaomao/`
- schema：2（`scan_runs` / `events`）
- 观察范围：仅 **website-main** 扫描。website-auth / website-integration 登记不扫。publish 未授权。
- 业务仓 HEAD（只读）：`1dae06ad7436`（`feat/website-backend-v0.1`）

## 调度

- `ai.xiaomao.scan`：plist `~/Library/LaunchAgents/ai.xiaomao.scan.plist`，mtime 2026-09-20 10:42，**未重写**。interval 300s。
- `ai.xiaomao.daily`：plist `~/Library/LaunchAgents/ai.xiaomao.daily.plist`，本机时区 21:30。`scripts/xiaomao-daily.sh` → `daily --scheduled`。无新证据不加载 30b。

## 命令

```
export PYTHONPATH=/Users/xiuqiu/WorkSpace/xiaomao-Agent/src
export XIAOMAO_HOME="$HOME/Library/Application Support/Xiaomao"

python3.11 -m xiaomao --home "$XIAOMAO_HOME" health
python3.11 -m xiaomao --home "$XIAOMAO_HOME" status --project website
python3.11 -m xiaomao --home "$XIAOMAO_HOME" latest daily
python3.11 -m xiaomao --home "$XIAOMAO_HOME" latest handoff --project website
python3.11 -m xiaomao --home "$XIAOMAO_HOME" pause infer
python3.11 -m xiaomao --home "$XIAOMAO_HOME" pause scan
```

本机 30b 对话（不经小猫 CLI）：终端输入 `qwen3`，退出 `/bye`。脚本：`scripts/qwen3.sh`；PATH：`~/.local/bin/qwen3`。

日报：`~/Library/Application Support/Xiaomao/reports/daily/`  
30b 副本：同目录 `YYYY-MM-DD.model.txt`  
Handoff：`~/Library/Application Support/Xiaomao/reports/handoff/`

## 试运行窗口

- 开始：2026-09-21T00:58:55+00:00
- 第 3 天核对：**2026-09-24**
- 第 7 天核对：**2026-09-28**
- 至少 3 个有真实开发输入的日期才够样本；否则保持 pending。
- 仓库清理不改变 Pilot 起算时间，也不删除观察数据。

## 暂停 / 恢复

- 停推理：`xiaomao pause infer`
- 停本系统定时任务：`xiaomao pause scan`（bootout scan+daily，不删 plist）
- 恢复：`xiaomao resume infer` / `xiaomao resume scan`
- 停 Ollama：`/bin/sh scripts/user_stop_ollama.sh`
- 不改整机睡眠策略，不 sudo。

## 不声称

- **不是 STABILITY_PASSED。**
- 不接第二棵工作树、QAI、远程测试/部署、Web UI、MCP、聊天界面。
- 不下载新模型。不重测 35b。
- 采集缺口（睡眠/关机/登出）会记 gap；缺口内未落盘编辑不可见，不编造。

## 验收

```
PYTHONPATH=src python3.11 scripts/accept.py
```

8 门须全过。accept 的 daily 门会写规则日报，30b 正文看 `.model.txt`。
