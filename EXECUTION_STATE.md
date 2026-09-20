# EXECUTION_STATE.md

更新时间：2026-09-20
状态：**USER_ACTION_REQUIRED**（未 DELIVERED：官方 Ollama CLI 尚未安装；沙盒拦出站，本会话终端页已满）

本文件是唯一交接状态记录。不要另开平行交接文档。

## 权限一次修正 + 一次复测（不再重复）

| 项 | 结果 |
|---|---|
| 错误键 `sandbox.allowWrite` | 已改为官方 `sandbox.filesystem.allowWrite`，备份 `.claude/settings.local.json.bak-20260920` |
| 外盘 `/Volumes/LocalDevData/Xiaomao` | MCP 授权后 **可写**（实测 WRITE_OK） |
| 正式 home `~/Library/Application Support/Xiaomao` | Python/Write 可写 |
| cwd `.git` | **仍 Operation not permitted**（官方保护路径）。未关沙盒。 |
| `git init` / baseline commit | 用户终端成功 |

## 已完成（有证据）

- unittest **34/34 PASS**（含模型目录复制不覆盖、源保留）
- `PYTHONPATH=src python3.11 scripts/accept.py`：**ok=true**，gates 全 true
  - commit `d019a09f4a26fbb59c5afef36538069dd3b6f660`
  - volume match `44c5480a-388c-475e-a320-a49b226a5953`
  - website-main 扫描前后 git 状态一致；第二次 `unchanged`/`inserted=false`
  - 日报：`~/Library/Application Support/Xiaomao/reports/daily/2026-09-20.txt`
  - Handoff：`~/Library/Application Support/Xiaomao/reports/handoff/website-20260920-110953.txt`
- sqlite 一致性迁移：源 `/Volumes/LocalDevData/Xiaomao/run/` 保留；归档 `backups/runtime-20260920T023153Z/`
- 用户级 LaunchAgent `ai.xiaomao.scan`：`StartInterval=300`，kickstart **runs=1 last exit=0**
- 并发锁：`scripts/check_lock.py` held+blocked+released
- **gemma4:12b 已复制**到 `/Volumes/LocalDevData/Xiaomao/ollama`（manifests+5 blobs，hash 一致）；源 `/Volumes/LocalDevData/Models/ollama` **保留**
- 正式 config `models_dir` 已改为 `/Volumes/LocalDevData/Xiaomao/ollama`
- 官方标签已核对：`qwen3.6:35b`（23GB Q4_K_M）、`qwen3-coder:30b`（19GB Q4_K_M）
- 官方 CLI 选择：`ollama-darwin.tgz` v0.34.2，sha256 `f33b2a5aa59bc6c961ed3ec23ba9dc646ca6d99ced8d2a0d46eb3a522167dd3f`
  - 不用 `install.sh`（会写 `/Applications`，可能 sudo `/usr/local/bin`）
  - 不用 Homebrew（官方下载页未列出）
  - 不写 `/Applications`、`/usr/local`、系统 daemon

## 阻塞（真实外部限制，不是停下来等批准）

沙盒出站 `github.com` / `ollama.com`：**403 blocked-by-allowlist / user denied**。
本会话 `run_in_terminal` 已开 6 页，不能再开。`open` / `osascript` 启动终端失败（-10810）。

**请在本会话已打开的终端页（tab 0 即可）粘贴一行：**

```bash
/bin/sh /Users/xiuqiu/WorkSpace/xiaomao-Agent/scripts/user_phase2.sh
```

该脚本会：

1. 用本次 `-c user.name/email` 提交剩余源码（不改全局 git）
2. 下载官方 `ollama-darwin.tgz` 到 `/Volumes/LocalDevData/Xiaomao/cache/`（约 151MB）
3. 校验 sha256 后解压到 `/Volumes/LocalDevData/Xiaomao/opt/ollama-v0.34.2/`，符号链接 `/Volumes/LocalDevData/Xiaomao/bin/ollama`
4. 以 `OLLAMA_HOST=127.0.0.1:11434`、`OLLAMA_MODELS=/Volumes/LocalDevData/Xiaomao/ollama`、`OLLAMA_NO_CLOUD=1`、`OLLAMA_MAX_LOADED_MODELS=1`、`OLLAMA_KEEP_ALIVE=0` 启动 **用户级** `ollama serve`
5. 打印进程环境（设置文件不算生效证据）

**不会：** sudo、写 `/Applications`、写 `/usr/local`、写系统 LaunchDaemon、改业务仓、关沙盒。

跑完后回会话说「Phase2 用户步骤已完成」。会话继续：进程 env 核验、gemma4 冒烟、顺序评测 35b→30b、模型日报/Handoff、总验收。

## 恢复

- sqlite 源：`/Volumes/LocalDevData/Xiaomao/run/` 与 backups
- 模型源：`/Volumes/LocalDevData/Models/ollama`（复制后未删）
- 停扫描：`launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/ai.xiaomao.scan.plist`
- 停 ollama（装好后）：`/bin/sh scripts/user_stop_ollama.sh`
- 不关沙盒、不 sudo、不改全局 Git、不写业务仓
