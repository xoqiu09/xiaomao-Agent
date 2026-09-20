# EXECUTION_STATE.md

更新时间：2026-09-20 12:56 CST
状态：**IN_PROGRESS（Phase 2：评测拉取中）**

本文件是唯一交接状态记录。不要另开平行交接文档。

## 当前

[xiaomao-agent-1](#local_3a5d5eaf-8aa8-482a-b0cf-1f6c46651617) 用户终端：INSTALL_OK → SERVE_OK pid=17807 → SMOKE_OK。正在跑 `user_eval_models.sh`（先 `qwen3.6:35b` 再 `qwen3-coder:30b`）。GOAL 不另开 curl / serve / pull / eval。

### 已核实证据

- tgz sha256 **match** `f33b2a5aa59bc6c961ed3ec23ba9dc646ca6d99ced8d2a0d46eb3a522167dd3f`
- CLI v0.34.2：`/Volumes/LocalDevData/Xiaomao/bin/ollama`
- serve pid **17807** 进程 env：HOST=127.0.0.1:11434 MODELS=/Volumes/LocalDevData/Xiaomao/ollama NO_CLOUD=1 MAX_LOADED=1 NUM_PARALLEL=1 KEEP_ALIVE=0 CONTEXT=8192
- 监听仅 127.0.0.1:11434
- 外盘 UUID **match** `44c5480a-388c-475e-a320-a49b226a5953`
- SMOKE_OK：gemma4:12b 生成「我没有 shell。」；llama-server `-c 8192`；unload 后 `ollama ps` 空；源目录未删
- Git HEAD `e95c8dd`（install lock / doctor 外盘 CLI / smoke 钉 8192）
- unittest 35/35 PASS

### 未完成

- 评测 JSON：`/Volumes/LocalDevData/Xiaomao/cache/eval-qwen3.6__35b.json` 与 `eval-qwen3-coder__30b.json`
- `--with-model` 日报/Handoff
- 模型路径后 `PYTHONPATH=src python3.11 scripts/accept.py`
- 3–7 天稳定性：待观察

## 恢复

- 停 ollama：`/bin/sh scripts/user_stop_ollama.sh`
- 停扫描：`launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/ai.xiaomao.scan.plist`
- 不关沙盒、不 sudo、不改全局 Git、不写业务仓
