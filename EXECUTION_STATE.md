# EXECUTION_STATE.md

更新时间：2026-09-20 15:05 CST
状态：**DELIVERED（当前验收门通过；3–7 天稳定性待观察）**

本文件是唯一交接状态记录。不要另开平行交接文档。

## 当前

v0.1 当前验收已用真实命令跑通。默认深度模型 **qwen3-coder:30b**；轻量 **gemma4:12b** 保留。模型无 shell、不写业务仓、不写数据库。证据栏程序生成。校验失败降级规则报告。扫描仍是用户级 LaunchAgent、规则路径、不加载模型。KEEP_ALIVE=0 不改。

### 已核实证据

- tgz sha256 **match**；CLI v0.34.2 外盘；serve pid **17807** 进程 env 完整；监听 127.0.0.1:11434；UUID `44c5480a-388c-475e-a320-a49b226a5953` match
- SMOKE_OK：gemma4:12b；n_ctx=8192；源目录未删
- **qwen3.6:35b 原评测（失败，保留）**：`/Volumes/LocalDevData/Xiaomao/cache/eval-qwen3.6_35b.json`（12146）n=22 ok=0 degraded=22 usable=0 serious=0 elapsed_s=1427.99，全 `invalid_json`（think 填满 num_predict）
- **qwen3.6:35b 重测（think:false）**：`/Volumes/LocalDevData/Xiaomao/cache/eval-qwen3.6_35b.retest.json`（17990）n=22 **ok=19 degraded=3 usable=19 serious_factual_errors=0 elapsed_s=1220.146**；parse_source content 22/22；swap 10.06M→21.25M。未覆盖原文件。
- **qwen3-coder:30b（胜者 / 默认深度）**：`/Volumes/LocalDevData/Xiaomao/cache/eval-qwen3-coder_30b.json`（17751）n=22 **ok=19 degraded=3 usable=19 serious_factual_errors=0 elapsed_s=100.152**；parse_source content 22/22；swap 5.75M→10.06M。质量并列 35b 重测，延迟约 12×，主机压力更低。
- 两边 3 条降级均为校验器拦住完成态措辞（「已上线 / 测试通过后」等），**未放宽校验器**。
- `--with-model` 日报/Handoff（证据 ID 修复后，默认 30b）：校验 **pass**。日报 `reports/daily/2026-09-20.txt`；Handoff `reports/handoff/website-20260920-150145.txt`。事实栏仍程序生成；测试/部署仍 unknown。
- `PYTHONPATH=src python3.11 scripts/accept.py`：**ok=true** ACCEPT_EXIT=0。产物 `/Volumes/LocalDevData/Xiaomao/cache/accept-20260920-1502.json`。gates：unittest / volume_match / formal_home / business_unchanged / scan_idempotent / daily / handoff / repo_git 全 true。业务仓 HEAD `1dae06ad7436` 与 index_mtime_ns 扫描前后不变。doctor 警告「工作树不存在：website-auth」（登记、scan=false，未挡门）。
- 形式配置 `depth_candidates: ["qwen3-coder:30b","qwen3.6:35b"]`；lite `gemma4:12b`
- unittest returncode 0（历史 39/39；accept 截尾是某用例 stdout JSON，以 returncode 为准）

### 未完成 / 不声称

- 3–7 天稳定性：待观察。本状态 **不** 把稳定性观察期算进当前验收。
- 不启动第三候选 / 70B / 120B。
- 不改 KEEP_ALIVE=0，不让扫描加载模型。

## 复现验收

```
PYTHONPATH=src python3.11 scripts/accept.py
```

模型路径（需 serve 已起、外盘已挂、默认深度 30b）：

```
python3.11 -m xiaomao --home "$HOME" daily --with-model
python3.11 -m xiaomao --home "$HOME" handoff --project website --with-model
```

（此处 `$HOME` 指 `~/Library/Application Support/Xiaomao`。）

## 恢复

- 停 ollama：`/bin/sh scripts/user_stop_ollama.sh`
- 停扫描：`launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/ai.xiaomao.scan.plist`
- 不关沙盒、不 sudo、不改全局 Git、不写业务仓
