# 小猫当前运行说明

核对时间：2026-09-30（Asia/Taipei）。**第一轮 Pilot 已 PILOT_ARCHIVED（观察范围不足，无稳定性结论）。**

## 源码、程序与数据身份

- 仓库：`/Users/xiuqiu/WorkSpace/xiaomao-Agent`，`main`，无 remote。原隔离源码 `~/.codex/worktrees/a45f/xiaomao-Agent` 已不存在，分支 `codex/xiaomao-latest-handoff` 已并入 main。
- 现用程序：两个 LaunchAgent 与 SwiftBar 插件链接都指向本仓工作区（09-23 起）。计划改为固定 tag 的独立 checkout，切换前本仓工作区即运行代码，不要在 main 上留未验收改动。
- 运行 Python：`/Users/xiuqiu/.local/bin/python3.11`，包版本 `0.1.0`；SwiftBar `2.1.1 (597)`。
- 正式数据：`~/Library/Application Support/Xiaomao/`，SQLite schema 2。模型目录在外盘 `/Volumes/LocalDevData/Xiaomao/ollama`。

## 已生效范围与调度

`config.json` 6 项，各 1 棵 main 树，全部启用扫描：QAI、wallet-core、xiaomao-Agent、xiuqiu-site、AI-Web3-Learning、Wallet-Infrastructure。公司、退役仓和第三方工具已于 09-22 移出；旧行与旧日报保留，不当作当前授权资料。

- `ai.xiaomao.scan`：300 秒一次，规则扫描，不加载模型。
- `ai.xiaomao.daily`：21:30，`daily --scheduled`。有未提交改动且无可复用摘要时调用 `qwen3-coder:30b`，否则只出规则文本。
- SwiftBar：`xiaomao.1m.sh` 每分钟只读刷新。原生菜单视觉与点击未验收。

测试、部署栏仍为 unknown：本版本不执行业务仓测试，也没有远程连接器。

## 验证边界

- 隔离验收：121 项测试、6 个隔离门 PASS（2026-09-30，工作区代码）；`formal_home`、`volume_match` 两门 `NOT_RUN`，`full_live_acceptance=false`。
- 模型 eval 证据：`/Volumes/LocalDevData/Xiaomao/cache/eval-*.json`（09-20）。gemma4:12b 无 eval 文件。

```sh
PYTHONPATH=src /Users/xiuqiu/.local/bin/python3.11 scripts/accept.py --isolated
```

## Pilot 记录

- 第一轮：2026-09-21T00:58:55Z 起算，2026-09-30T14:40:26Z 以 `pilot archive` 归档。原因：只观察各仓主树，Wallet-Infrastructure 09-23 起的提交都在其他工作树，无法按「至少 3 个有真实开发输入的日期」判断。窗口计数写在 `pilot_archived` 事件里：扫描成功 14,491 / 错误 323（全在 09-21 ~ 22，来自已移出的登记）/ 跳过 2。
- 下一轮：多工作树观察上线后 `pilot start` 重新起算。

## 暂停与接续

`pause infer` 暂停可选推理，`pause scan` 停扫描与日报；对应 `resume` 恢复。不要删除历史 SQLite 或手改 meta 来解决显示问题。

切换运行源码时：先建新 checkout 并在其中跑隔离验收，再改两个 plist 与插件链接，核对 `launchctl print` 与一次真实扫描；旧入口保留到新入口验证完成。

架构和维护方式见 [README.md](README.md)、[ARCHITECTURE.md](ARCHITECTURE.md)、[CONTRIBUTING.md](CONTRIBUTING.md)。
