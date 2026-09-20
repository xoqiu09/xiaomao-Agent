# 小猫 / xiaomao-agent

本机只读工程观察员。持续工作的是采集程序；本地模型只在生成日报或交接材料时加载。

```text
授权工作树 → 只读 Git 采集 → 路径/密钥过滤 → SQLite → 规则报告
                         ↘ 日终/手动：可选本地模型（失败则降级）
```

## 命令

```bash
PYTHONPATH=src python3.11 -m unittest discover -s tests -v
PYTHONPATH=src python3.11 -m xiaomao doctor
PYTHONPATH=src python3.11 -m xiaomao init
PYTHONPATH=src python3.11 -m xiaomao scan --project website
PYTHONPATH=src python3.11 -m xiaomao status --project website
PYTHONPATH=src python3.11 -m xiaomao daily
PYTHONPATH=src python3.11 -m xiaomao handoff --project website
PYTHONPATH=src python3.11 -m xiaomao eval --dry-run
PYTHONPATH=src python3.11 -m xiaomao schedule status
PYTHONPATH=src python3.11 scripts/accept.py
```

数据目录默认 `~/Library/Application Support/Xiaomao/`。测试用 `--home` 覆盖。

## 调度

用户级 LaunchAgent `ai.xiaomao.scan`，每 5 分钟只跑 `scan`（不加载模型）。

```bash
PYTHONPATH=src python3.11 -m xiaomao schedule install
launchctl kickstart -k "gui/$(id -u)/ai.xiaomao.scan"
PYTHONPATH=src python3.11 -m xiaomao schedule uninstall
```

日志：`~/Library/Application Support/Xiaomao/logs/scan.{out,err}.log`

## 不变约束

- 采集与推理解耦；事实、解读、建议、未知分栏
- 无证据 = unknown；旧测试报告不能升级为当前通过
- 模型没有 shell、不能写 Git / 数据库
- 外盘不在或身份不符时不把模型改下到 `~/.ollama/models`
- 新工作树只列为候选，不自动授权
- 3–7 天稳定性本轮标记为待观察

深度模型候选（需本机评测后选定）：`qwen3.6:35b`、`qwen3-coder:30b`。轻量基线 `gemma4:12b` 保留，目录 `/Volumes/LocalDevData/Xiaomao/ollama`。

官方 CLI 只装一种：外盘 `ollama-darwin.tgz`（`scripts/user_install_ollama.sh`），不 sudo、不写 `/Applications`。
