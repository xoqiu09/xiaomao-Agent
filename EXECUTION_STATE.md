# EXECUTION_STATE.md

更新时间：2026-09-20 12:55 CST
状态：**IN_PROGRESS（Phase 2：serve 已起，冒烟进行中）**

本文件是唯一交接状态记录。不要另开平行交接文档。

## 当前

同仓会话 [xiaomao-agent-1](#local_3a5d5eaf-8aa8-482a-b0cf-1f6c46651617) 在用户终端完成官方 CLI 续传安装；GOAL 本会话沙盒不能 bind/connect `127.0.0.1:11434`，serve/冒烟/评测走对端用户终端。不双开 curl、不双开 serve。

### 已核实证据

- tgz sha256 **match** `f33b2a5aa59bc6c961ed3ec23ba9dc646ca6d99ced8d2a0d46eb3a522167dd3f`（158526160 bytes）
- CLI：`/Volumes/LocalDevData/Xiaomao/bin/ollama` → `.../opt/ollama-v0.34.2/ollama`  **v0.34.2**
- 未写 `/Applications` / `/usr/local` / LaunchDaemons；无 sudo
- serve pidfile **17807**，用户终端启动
  - `OLLAMA_HOST=127.0.0.1:11434`
  - `OLLAMA_MODELS=/Volumes/LocalDevData/Xiaomao/ollama`
  - `OLLAMA_NO_CLOUD=1` / log `Ollama cloud disabled: true`
  - `OLLAMA_MAX_LOADED_MODELS=1` `OLLAMA_NUM_PARALLEL=1` `OLLAMA_KEEP_ALIVE=0` `OLLAMA_CONTEXT_LENGTH=8192`
  - lsof：`TCP 127.0.0.1:11434 (LISTEN)` IPv4 loopback
  - log 另有 `default_num_ctx=262144`（VRAM 建议）；评测请求必须钉 `num_ctx=8192`
- 外盘 UUID **match** `44c5480a-388c-475e-a320-a49b226a5953`
- gemma4:12b 仍在模型目录（7.6 GB）；源 `/Volumes/LocalDevData/Models/ollama` 保留
- `~/.ollama` 仅 serve 生成了 `id_ed25519`；**没有** `~/.ollama/models`
- 冒烟：对端已观察到生成「我没有 shell。」且 `ollama ps` 为空（已卸载）；等 `SMOKE_OK`

### 沙盒限制（本会话，不重试无新证据的通道）

| 通道 | 结果 |
|---|---|
| Bash bind/connect `127.0.0.1:11434` | operation not permitted |
| Bash `github.com` | 403 allowlist |
| `run_in_terminal` 第 7 页 | 拒绝 |
| `ps` / `pgrep` 于沙盒 | Operation not permitted |

## 已完成（有证据）

- unittest **35/35 PASS**
- Git：`d019a09` baseline；`e87dd12` 模型目录；`55fc25f` 续传安装/评测脚本
- LaunchAgent `ai.xiaomao.scan` StartInterval=300，规则扫描仍在跑（scan 脚本保持 `exec ... scan`，不加载模型）
- 规则日报/Handoff 已存在；`--with-model` 待评测后跑
- website-main HEAD 此前扫描前后均为 `1dae06ad743684f1f3d9e676e04ecc72f959e0ce`

## 未完成

- gemma4 冒烟收口（SMOKE_OK）
- `qwen3.6:35b` → `qwen3-coder:30b` 评测 JSON
- `--with-model` 日报/Handoff
- 模型路径后重跑 `scripts/accept.py`
- 剩余源码提交（install lock、doctor 认外盘 CLI、smoke 钉 8192）

## 恢复

- CLI：`/Volumes/LocalDevData/Xiaomao/bin/ollama`
- 停 ollama：`/bin/sh scripts/user_stop_ollama.sh`
- 停扫描：`launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/ai.xiaomao.scan.plist`
- 不关沙盒、不 sudo、不改全局 Git、不写业务仓
- 3–7 天稳定性：待观察
