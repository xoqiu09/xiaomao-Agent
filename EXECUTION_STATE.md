# EXECUTION_STATE.md

更新时间：2026-09-20 14:36 CST
状态：**IN_PROGRESS（Phase 2：30b 胜出；35b 重测进行中）**

本文件是唯一交接状态记录。不要另开平行交接文档。

## 当前

[xiaomao-agent-1](#local_3a5d5eaf-8aa8-482a-b0cf-1f6c46651617) 已结束 `user_eval_models.sh`（EVAL_SEQUENCE_OK）。GOAL 正在 `user_retest_qwen35.sh`。serve pid 17807。不另开 curl / serve / pull。

### 已核实证据

- tgz sha256 **match**；CLI v0.34.2 外盘；serve pid **17807** 进程 env 完整；监听 127.0.0.1:11434；UUID match
- SMOKE_OK：gemma4:12b；n_ctx=8192；源目录未删
- **qwen3.6:35b 原评测（失败，保留）**：`/Volumes/LocalDevData/Xiaomao/cache/eval-qwen3.6_35b.json` n=22 ok=0 degraded=22 usable=0 serious=0 elapsed_s=1427.99，全 `invalid_json`
- **qwen3-coder:30b（胜者）**：`/Volumes/LocalDevData/Xiaomao/cache/eval-qwen3-coder_30b.json` n=22 **ok=19 degraded=3 usable=19 serious_factual_errors=0 elapsed_s=100.152**；parse_source content 22/22；thinking=0；swap 5.75M→10.06M；3 条 `unwarranted_completion`（校验器拦住「是否已上线 / 测试通过后」）。不伪造 22/22。
- `--with-model` 日报/Handoff 已写出并**降级**：模型引用 `worktree:*` 当时不在 evidence_ids。已修 `facts_payload` 发出 `worktree:<id>`，默认深度模型改为 `qwen3-coder:30b`。校验器仍拒绝「确认是否已上线」。
- Git HEAD `4afadd2`
- unittest 39/39 PASS

### 未完成

- 35b 重测 → `eval-qwen3.6_35b.retest.json`（进行中）
- 用 30b 再跑 `--with-model` daily/handoff（证据 ID 修复后）
- `PYTHONPATH=src python3.11 scripts/accept.py`
- 3–7 天稳定性：待观察

## 恢复

- 停 ollama：`/bin/sh scripts/user_stop_ollama.sh`
- 停扫描：`launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/ai.xiaomao.scan.plist`
- 不关沙盒、不 sudo、不改全局 Git、不写业务仓
