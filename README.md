# 小猫 / xiaomao-agent

本机只读工程观察员。持续工作的是采集程序；本地模型只在生成日报或交接材料时加载。

```text
授权工作树 → 只读 Git 采集 → 路径/密钥过滤 → SQLite → 规则报告
                         ↘ 日终/手动：可选本地模型（失败则降级）
```

数据目录默认 `~/Library/Application Support/Xiaomao/`。测试用 `--home` 覆盖。
下面命令默认 `PYTHONPATH=src`，`python3.11` 为 `/Users/xiuqiu/.local/bin/python3.11`。

## 明天怎么用

```bash
export PYTHONPATH=/Users/xiuqiu/WorkSpace/xiaomao-Agent/src
export XIAOMAO_HOME="$HOME/Library/Application Support/Xiaomao"

python3.11 -m xiaomao --home "$XIAOMAO_HOME" health
python3.11 -m xiaomao --home "$XIAOMAO_HOME" status --project website
python3.11 -m xiaomao --home "$XIAOMAO_HOME" latest daily
python3.11 -m xiaomao --home "$XIAOMAO_HOME" latest daily --print
python3.11 -m xiaomao --home "$XIAOMAO_HOME" open daily
python3.11 -m xiaomao --home "$XIAOMAO_HOME" handoff --project website
python3.11 -m xiaomao --home "$XIAOMAO_HOME" latest handoff --project website
python3.11 -m xiaomao --home "$XIAOMAO_HOME" open handoff --project website
```

绝对路径：

- 日报：`~/Library/Application Support/Xiaomao/reports/daily/`
- 30b 日报副本（不被规则日报覆盖）：`~/Library/Application Support/Xiaomao/reports/daily/YYYY-MM-DD.model.txt`
- Handoff：`~/Library/Application Support/Xiaomao/reports/handoff/`
- 状态报告：`~/Library/Application Support/Xiaomao/reports/projects/`
- 库：`~/Library/Application Support/Xiaomao/xiaomao.sqlite`
- 扫描日志：`~/Library/Application Support/Xiaomao/logs/scan.{out,err}.log`
- 日报日志：`~/Library/Application Support/Xiaomao/logs/daily.{out,err}.log`

手动加载 30b（无新证据时调度不会自动加载）：

```bash
python3.11 -m xiaomao --home "$XIAOMAO_HOME" daily --with-model
python3.11 -m xiaomao --home "$XIAOMAO_HOME" handoff --project website --with-model
```

## 暂停 / 恢复

```bash
# 只停推理：扫描继续，不加载模型
python3.11 -m xiaomao --home "$XIAOMAO_HOME" pause infer
python3.11 -m xiaomao --home "$XIAOMAO_HOME" resume infer

# 停本系统定时任务（scan + daily LaunchAgent，不删 plist）
python3.11 -m xiaomao --home "$XIAOMAO_HOME" pause scan
python3.11 -m xiaomao --home "$XIAOMAO_HOME" resume scan
```

等价 launchctl：

```bash
launchctl bootout "gui/$(id -u)" ~/Library/LaunchAgents/ai.xiaomao.scan.plist
launchctl bootout "gui/$(id -u)" ~/Library/LaunchAgents/ai.xiaomao.daily.plist
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/ai.xiaomao.scan.plist
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/ai.xiaomao.daily.plist
```

停 Ollama：`/bin/sh scripts/user_stop_ollama.sh`

## 调度

- `ai.xiaomao.scan`：每 5 分钟只跑 `scan`（不加载模型）。已装，不要再装一份。
- `ai.xiaomao.daily`：本机时区 **21:30** 跑 `daily --scheduled`。无新磁盘证据不加载模型，只写短规则日报。可改 plist 的 `StartCalendarInterval`。

```bash
python3.11 -m xiaomao schedule status
python3.11 -m xiaomao schedule status-daily
python3.11 -m xiaomao schedule install-daily   # 仅当 daily 未装
```

## 验收

```bash
PYTHONPATH=src python3.11 -m unittest discover -s tests -v
PYTHONPATH=src python3.11 scripts/accept.py
```

## 不变约束

- 采集与推理解耦；事实、解读、建议、未知分栏
- 无证据 = unknown；旧测试报告不能升级为当前通过
- 模型没有 shell、不能写 Git / 数据库
- 外盘不在或身份不符时不把模型改下到 `~/.ollama/models`
- 新工作树只列为候选，不自动授权
- 默认深度 **qwen3-coder:30b**；`gemma4:12b` 与 `qwen3.6:35b` 保留，不自动调用、不重测、不删除
- KEEP_ALIVE=0 不改；扫描路径不加载模型
- 无变化写「授权观察范围内无新变化」，不写「用户今天没有工作」
- 本轮状态是 **PILOT_RUNNING**，不是 STABILITY_PASSED
