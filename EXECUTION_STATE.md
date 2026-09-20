# EXECUTION_STATE.md

更新时间：2026-09-20 14:22 CST
状态：**IN_PROGRESS（Phase 2：30b 唯一 pull；JSON 提取已合入）**

本文件是唯一交接状态记录。不要另开平行交接文档。

## 当前

[xiaomao-agent-1](#local_3a5d5eaf-8aa8-482a-b0cf-1f6c46651617) 用户终端：INSTALL_OK → SERVE_OK pid=17807 → SMOKE_OK。`user_eval_models.sh` 已完成 `qwen3.6:35b` 评测，正在唯一一条 `qwen3-coder:30b` pull。GOAL 不另开 curl / serve / pull / eval。

### 已核实证据

- tgz sha256 **match** `f33b2a5aa59bc6c961ed3ec23ba9dc646ca6d99ced8d2a0d46eb3a522167dd3f`
- CLI v0.34.2：`/Volumes/LocalDevData/Xiaomao/bin/ollama`
- serve pid **17807** 进程 env：HOST=127.0.0.1:11434 MODELS=/Volumes/LocalDevData/Xiaomao/ollama NO_CLOUD=1 MAX_LOADED=1 NUM_PARALLEL=1 KEEP_ALIVE=0 CONTEXT=8192
- 监听仅 127.0.0.1:11434
- 外盘 UUID **match** `44c5480a-388c-475e-a320-a49b226a5953`
- SMOKE_OK：gemma4:12b；llama-server `-c 8192`；源目录未删
- `qwen3.6:35b` 已落地（blob d372de8e9348）；评测 `/Volumes/LocalDevData/Xiaomao/cache/eval-qwen3.6_35b.json`：**n=22 ok=0 degraded=22 usable=0 serious_factual_errors=0 elapsed_s=1427.99**，22 条 `invalid_json`。不伪造 pass。
- host_pressure：swap 0→5.75M / 1024M；pages_compressor 902683→1590391
- 日志：每条 `eval time … / 1024 tokens`（thinking 吃光 `num_predict`，content 空/残缺）
- 提取修复已合入：`think: false`（chat 顶层，见 `chat_json_payload`）、`num_predict=2048`、`temperature=0`；`extract_json_object` 从 content/thinking 抽对象。评测行记 `parse_source` / `eval_count`。**校验器未放宽**。
- unittest **36/36 PASS**（含 extract + think:false payload 钉死）
- Git HEAD `da09507`（`bb2d78b` 提取 / `547e573` 诊断字段 / `da09507` payload 钉死）

### 未完成

- 唯一 `qwen3-coder:30b` pull（blob `1194192cf2a1`；逻辑 17.282G，alloc 约 14.7G 仍在涨）。勿另开第二条。
- 30b 评测 JSON（期望 `eval-qwen3-coder_30b.json`）；用新提取重测 35b
- 按本项目任务质量选胜者；两者仍失败则再修输入组织/提示后重测，不降安全/事实标准
- `--with-model` 日报/Handoff
- 模型路径后 `PYTHONPATH=src python3.11 scripts/accept.py`
- 3–7 天稳定性：待观察

## 恢复

- 停 ollama：`/bin/sh scripts/user_stop_ollama.sh`
- 停扫描：`launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/ai.xiaomao.scan.plist`
- 不关沙盒、不 sudo、不改全局 Git、不写业务仓
