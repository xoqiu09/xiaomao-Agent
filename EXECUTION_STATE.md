# EXECUTION_STATE.md

更新时间：2026-09-20 14:32 CST
状态：**IN_PROGRESS（Phase 2：30b 评测进行中；think:false 已生效）**

本文件是唯一交接状态记录。不要另开平行交接文档。

## 当前

[xiaomao-agent-1](#local_3a5d5eaf-8aa8-482a-b0cf-1f6c46651617) 用户终端：INSTALL_OK → SERVE_OK pid=17807 → SMOKE_OK → 35b 评测（失败）→ 30b pull 完成 → 30b eval 进行中。GOAL 不另开 curl / serve / pull / eval。

### 已核实证据

- tgz sha256 **match** `f33b2a5aa59bc6c961ed3ec23ba9dc646ca6d99ced8d2a0d46eb3a522167dd3f`
- CLI v0.34.2：`/Volumes/LocalDevData/Xiaomao/bin/ollama`
- serve pid **17807** 进程 env：HOST=127.0.0.1:11434 MODELS=/Volumes/LocalDevData/Xiaomao/ollama NO_CLOUD=1 MAX_LOADED=1 NUM_PARALLEL=1 KEEP_ALIVE=0 CONTEXT=8192
- 监听仅 127.0.0.1:11434
- 外盘 UUID **match** `44c5480a-388c-475e-a320-a49b226a5953`
- SMOKE_OK：gemma4:12b；llama-server `-c 8192`；源目录未删
- `qwen3.6:35b` 评测 `/Volumes/LocalDevData/Xiaomao/cache/eval-qwen3.6_35b.json`：**n=22 ok=0 degraded=22 usable=0 serious_factual_errors=0 elapsed_s=1427.99**，22 条 `invalid_json`。不伪造 pass，不覆盖该文件。
- `qwen3-coder:30b` manifest 已落；主层 blob `1194192cf2a1` 18556688736 字节，无 partial。eval 文件已创建、stdout 重定向下仍 0 字节。
- 30b 运行时：`thinking = 0`、`temp = 0.000`、`n_ctx_slot = 8192`、49/49 GPU、MTL0 ~17524 MiB。首几条 **eval ~134–146 tokens / ~1.2–1.4s**（对比 35b 旧跑打满 1024）。KEEP_ALIVE=0 每条 `load_tensors` 重载，属预期。
- Git HEAD `6ca9874`：think:false + extract_json_object + parse_source + 300s 超时。**校验器未放宽**。
- unittest 提取/payload 钉死已过。

### 未完成

- 等 `eval-qwen3-coder_30b.json` 写完（22 条 × KEEP_ALIVE=0 重载，约 20–30 分钟）
- 用 `scripts/user_retest_qwen35.sh` 重测 35b → `eval-qwen3.6_35b.retest.json`
- 按本项目任务质量选胜者；两者仍失败则再修输入组织/提示后重测，不降安全/事实标准
- `--with-model` 日报/Handoff
- 模型路径后 `PYTHONPATH=src python3.11 scripts/accept.py`
- 3–7 天稳定性：待观察

## 恢复

- 停 ollama：`/bin/sh scripts/user_stop_ollama.sh`
- 停扫描：`launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/ai.xiaomao.scan.plist`
- 不关沙盒、不 sudo、不改全局 Git、不写业务仓
