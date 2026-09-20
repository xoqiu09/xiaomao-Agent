# EXECUTION_STATE.md

更新时间：2026-09-20
状态：**IN_PROGRESS**（未 DELIVERED：Ollama 未装，本仓尚无 baseline commit）

本文件是唯一交接状态记录。不要另开平行交接文档。

## 权限一次修正 + 一次复测（不再重复）

| 项 | 结果 |
|---|---|
| 错误键 `sandbox.allowWrite` | 已改为官方 `sandbox.filesystem.allowWrite`，备份 `.claude/settings.local.json.bak-20260920` |
| 外盘 `/Volumes/LocalDevData/Xiaomao` | MCP 授权后 **可写**（实测 WRITE_OK） |
| 正式 home `~/Library/Application Support/Xiaomao` | Python/Write 可写；Bash 沙盒曾拒，MCP 授权用户主目录后可写 |
| cwd `.git` | **仍 Operation not permitted**（官方保护路径，allowWrite 不能豁免）。未关沙盒。 |
| `git init` | 沙盒失败一次后停止该假设；改在用户终端执行，**已成功** |

## 已完成（有证据）

- unittest **33/33 PASS**（`PYTHONPATH=src python3.11 -m unittest discover -s tests -v`）
- sqlite 一致性迁移：`VACUUM`/backup API → 正式 home
  - 源保留：`/Volumes/LocalDevData/Xiaomao/run/xiaomao.sqlite`
  - 归档：`/Volumes/LocalDevData/Xiaomao/backups/runtime-20260920T023153Z/xiaomao.sqlite` integrity=ok，行数一致
  - 正式库：`~/Library/Application Support/Xiaomao/xiaomao.sqlite` 122880 bytes
- 外盘 UUID **match** `44c5480a-388c-475e-a320-a49b226a5953`
- website-main 扫描 ×2：`unchanged` / `inserted=false`；HEAD/branch/status/index mtime 扫描前后一致
- 规则日报：`~/Library/Application Support/Xiaomao/reports/daily/2026-09-20.txt`
- 规则 Handoff：`~/Library/Application Support/Xiaomao/reports/handoff/website-20260920-103448.txt`
- 用户级 LaunchAgent `ai.xiaomao.scan`：plist 已写，`bootstrap` 成功，`kickstart` **runs=1 last exit=0**，日志可查
- 并发锁：`scripts/check_lock.py` held+blocked+released
- 模型标签已官方核对：`qwen3.6:35b`（23GB Q4_K_M）、`qwen3-coder:30b`（19GB Q4_K_M）

## 阻塞 / 待完成

- **Ollama 未安装**。沙盒出站到 `ollama.com` 被拒（blocked-by-allowlist）。需在用户终端用官方 macOS 安装包安装**一种**方式。
- **本仓 baseline commit**：`.git` 已存在，待 `git add`/`commit`（沙盒可能仍拦 `.git/config`/`hooks`；提交也走用户终端）。
- website-auth 路径当前 **MISSING**（登记但不扫描；doctor 警告）。不扩大授权。
- 3–7 天稳定性：**待观察**，不作为本轮 DELIVERED 条件。

## 恢复

- sqlite 源：`/Volumes/LocalDevData/Xiaomao/run/` 与 backups 目录
- 停扫描：`launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/ai.xiaomao.scan.plist`
- 卸任务：`PYTHONPATH=src python3.11 -m xiaomao schedule uninstall`
- 不关沙盒、不 sudo、不改全局 Git、不写业务仓

## USER_ACTION_REQUIRED（Ollama）

官方 macOS 安装（二选一，只装一种，优先 App）：

1. 打开 https://ollama.com/download/mac 下载 `Ollama.dmg`，拖到 `/Applications`。
2. 或：`curl -fsSL https://ollama.com/install.sh | sh`

装好后回会话说「Ollama 已安装」，继续：迁移模型目录、进程级 env 核验、评测 `qwen3.6:35b` → `qwen3-coder:30b`。
