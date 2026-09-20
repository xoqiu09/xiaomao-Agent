# PRECHECK.md — 阶段 0 实测 + 阶段 1 交付记录

调研/预检日期：2026-09-20  
本文件写在独立仓库 `xiaomao-Agent`，不写入任何业务仓。

## 1. 已验证硬件 / 系统 / 存储

| 项 | 实测 | 未知 |
|---|---|---|
| 机器 | Mac Studio Mac16,9，Apple M4 Max，16 核（12P+4E） | — |
| 内存 | 64 GB 统一内存；预检时 swapins/swapouts = 0 | 与 41GB 开发负载叠跑 35B 的峰值只能实测 |
| 系统 | macOS 27.0 (26A428)，时区 Asia/Taipei | — |
| 内置盘 | APFS Data 约 460GB，已用约 211GB，剩余约 224GB | — |
| 外盘 | `/Volumes/LocalDevData` APFS，约 1.86TB / 剩余 1.62TB，`/dev/disk7s1` | Volume UUID、SSD/HDD、雷雳/USB（`diskutil` 在沙盒中不可用） |
| 外盘已有模型 | `Models/ollama` 7.0GB，仅 `gemma4:12b` | — |

缓存文件可被覆盖，不能把「64−已用」当成模型预算。

## 2. 现有运行时（未安装任何东西来补齐）

| 工具 | 状态 |
|---|---|
| Python | Homebrew 3.14.7；uv 管理的 **3.11.15**（本项目使用） |
| uv | 0.12.1 |
| Git | Apple Git 2.50.1 |
| SQLite | 3.54 / Python 3.53.4，**FTS5 可用** |
| Ollama | **未安装**（无 CLI、无 Ollama.app）。`~/.ollama` 空目录 |
| pytest | 本机未装；PyPI 本轮被拒绝。阶段 1 测试用 `python -m unittest` |

## 3. 目录与首个授权工作树

代码：`/Users/xiuqiu/WorkSpace/xiaomao-Agent`  
数据：`~/Library/Application Support/Xiaomao/`  
外盘数据：`/Volumes/LocalDevData/Xiaomao/`  
模型：沿用 `/Volumes/LocalDevData/Models/ollama/`（不另造第二份）

试点项目 `website`（The AI 官网后端）：

| worktree_id | 路径 | 阶段 1 |
|---|---|---|
| website-main | `/Users/xiuqiu/WorkSpace/theAIapp-service` | **扫描** |
| website-auth | `.../theAIapp-service-auth-publishing` | 只登记 |
| website-integration | `.../theAIapp-service-integration` | 只登记 |
| theAIapp-service-publish | detached | **不授权** |

只读策略：参数数组启动 git、`GIT_OPTIONAL_LOCKS=0`、禁用 pager/hooks/external diff/LFS smudge；不 fetch、不跑测试、不读 `.env` 正文。

## 4. 模型预算（阶段 2 才下载，本轮未拉）

保留现有 `gemma4:12b`。第一批对照：

1. `qwen3.6:35b`（~23GB，优先）
2. `qwen3-coder:30b`（~19GB）

同时只加载 1 个模型；`OLLAMA_CONTEXT_LENGTH=8192`；`keep_alive=0`；`OLLAMA_NO_CLOUD=1`。外盘缺失禁止改下到 `~/.ollama/models`。

## 5. 阶段 1 模块与测试

已实现：`doctor / init / scan / status / storage`、SQLite schema、只读 Git、路径/密钥门、规则报告。

```text
PYTHONPATH=src python3.11 -m unittest discover -s tests -v
Ran 11 tests in ~1.4s  OK
```

覆盖：unborn、detached、staged/unstaged/untracked、重复扫描幂等、`.env` 不进摘录、新工作树不自动授权、越界 symlink 不读目标内容。

## 6. 对主工作树的首次只读扫描（已执行）

2026-09-20 实测，数据目录用外盘 `/Volumes/LocalDevData/Xiaomao/run`（内置 Application Support 被本会话沙盒限制写 sqlite）。

```text
website-main  HEAD 1dae06ad7436  refs/heads/feat/website-backend-v0.1
collection_status ok   staged 0 / unstaged 0 / untracked 0
测试 unknown   部署 unknown
新发现未授权工作树：/Users/xiuqiu/WorkSpace/theAIapp-service-publish（authorized=0）
第二次扫描：status=unchanged，inserted=false（无重复写库）
```

业务仓零影响，扫描前后一致：

```text
HEAD        1dae06ad743684f1f3d9e676e04ecc72f959e0ce  →  相同
index mtime 1789795454                                →  相同
git status  空                                         →  空
```

`evidence` 表 0 行（工作区 clean），数据库中未匹配到私钥 / AKIA / sk- 等密钥模式。运行数据占用约 126KB，远低于 2GB 预算。

## 7. 阶段 1.5 环境收尾

### 7.1 外盘身份校验：已解决

`diskutil` 依赖 DiskManagement / DiskArbitration 框架，在本会话沙盒中不可用（错误原文：`Unable to run because unable to use the DiskManagement framework.`）。

改用 `getattrlist(2)` 系统调用直接读 APFS 卷 UUID —— 普通只读 syscall，不用 sudo、不降级任何安全策略。新增 `src/xiaomao/volume.py`。

```text
/Volumes/LocalDevData  →  44c5480a-388c-475e-a320-a49b226a5953
```

已写入 `config.json`。`volume_present()` 现按身份判断，不再只看路径名：

| 情况 | status | 是否可用 |
|---|---|---|
| UUID 一致 | `match` | 可用 |
| UUID 不一致（换了别的盘） | `mismatch` | **拒绝** |
| 配置里没写 UUID | `unverified` | 可用但告警 |
| 挂载点不存在 | — | 不可用 |

实测：正确 UUID → `match/usable=True`；伪造 UUID → `mismatch/usable=False`。

### 7.2 仍被本会话沙盒拒绝的两项

已定位到**具体来源**：是 Claude Code 的 Bash 沙盒写白名单，不是 macOS TCC、不是 POSIX 权限、不是 SIP。

判别证据：

```text
$HOME/.xiaomao-probe          → Operation not permitted
~/Documents/xiaomao-probe     → Operation not permitted
~/Library/Application Support → Operation not permitted（父目录同样被拒）
$TMPDIR                       → 可写，git init 成功
/Volumes/LocalDevData         → 可写，git init 成功（已单独授权）
.gitzz / .probedot（同目录）  → 可建
.git（同目录）                → Operation not permitted
```

即：同一个目录里 `.gitzz` 能建、`.git` 不能建，说明是按路径名的定向拦截，与 git 本身无关。

| 被拦动作 | 真实来源 | 影响 | 需要你做什么 |
|---|---|---|---|
| 本仓库 `git init`（创建 `.git`） | Bash 沙盒 `denyWithinAllow` 命中 `.git` | 仓库仍未版本管理 | Claude Code 的 Bash 沙盒设置里放开本项目 `.git` 写入 |
| `~/Library/Application Support/**` 写入 | Bash 沙盒 `write.allowOnly` 未包含该路径 | sqlite 仍在外盘 `Xiaomao/run` | 把该目录加入 Bash 沙盒可写路径 |

两项都不涉及 macOS 系统设置，不需要改「完全磁盘访问」或关 SIP。文件内容可经工具写入（`config.json` 即如此），只有 Bash 进程被拦。

`pytest` / PyPI 按你的指示不作为阻塞项，测试继续用 `python -m unittest`，未改动任何全局 Python 环境。

### 7.3 重新验证结果

```text
unittest            17/17 PASS（新增 6 条 volume 身份测试）
doctor              外盘身份 match，UUID 告警消失
scan × 4            仅 1 条 observation，后续均 unchanged/inserted=false
```

业务仓 `theAIapp-service` 扫描前后逐项一致：

```text
HEAD        1dae06ad743684f1f3d9e676e04ecc72f959e0ce   相同
branch      feat/website-backend-v0.1                  相同
index mtime 1789795454                                 相同
index sha256 253d4fc8d26fa8ff…                         相同
git status  空                                          空
reflog top  1dae06ad7436…                              相同
```

## 8. 尚未授权（不要擅自做）

- 安装 Ollama / 改 Ollama 进程环境变量
- 下载 `qwen3.6:35b` 或 `qwen3-coder:30b`
- 注册 LaunchAgent
- 扫描 integration 工作树的 23 条 dirty 正文
- 接入 QAI 或其他项目
