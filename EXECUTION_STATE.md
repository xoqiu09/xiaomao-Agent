# 小猫当前运行说明

核对时间：2026-09-22 19:40（Asia/Taipei）。**PILOT_RUNNING，未通过多日稳定性验收。**

## 源码、程序与数据身份

- 原仓：`/Users/xiuqiu/WorkSpace/xiaomao-Agent`，`main`，HEAD `7ad0fd8147b04b8c9b94354ad684556ecb6fe6b5`，无 remote。原有 16 个未提交路径及内容全部保留，未把本轮实现合回原仓。
- 现用实现位于隔离源码：`/Users/xiuqiu/.codex/worktrees/a45f/xiaomao-Agent`，分支 `codex/xiaomao-latest-handoff`。继承快照提交 `669eb7682aecd291244c2410b21b27d9f2ce8007`；产品代码提交 `0a2478993a3117402071bf81fbb4d2e1820c183f`。本运行说明的后续提交只更新文档；最新完整 HEAD 用该目录的 `git rev-parse HEAD` 获取。
- 运行 Python：`/Users/xiuqiu/.local/bin/python3.11`（3.11.15），包版本 `0.1.0`；SwiftBar `2.1.1 (597)`。
- 正式数据：`~/Library/Application Support/Xiaomao/`，SQLite schema 2。源码、SQLite、报告和模型是分开的实物。
- 上述隔离源码已经被启动项和插件依赖，接手或清理 worktree 时必须保留它；不能把“原仓未合并”误解为“隔离树尚未使用”。

## 已生效范围与调度

正式 `config.json` 现有 6 项 / 6 棵启用树：QAI、wallet-core、xiaomao-Agent、xiuqiu-site、AI-Web3-Learning、Wallet-Infrastructure。小猫自身的观察对象仍为原仓，运行程序来自隔离源码；两者用途不同。

公司、退役仓、归档兼容链接和第三方工具登记已移出活动清单。旧数据库及报告保留，不将旧行或旧日报重新当作当前授权资料。

- `~/Library/LaunchAgents/ai.xiaomao.scan.plist` 指向隔离源码 `scripts/xiaomao-scan.sh`，300 秒一次。
- `~/Library/LaunchAgents/ai.xiaomao.daily.plist` 指向隔离源码 `scripts/xiaomao-daily.sh`，本机时区 21:30。原有调度频率和 `RunAtLoad=false` 保留。
- `~/Library/Application Support/Xiaomao/swiftbar-plugins/xiaomao.1m.sh` 已链接隔离源码的同名插件，每分钟读取菜单。

切换后首轮正式扫描 6 项全部成功，6 条范围证明与扫描记录对应；没有触发日报模型推理。正式 home 下小猫项目的生成交接、读取交接和已安装插件脚本运行通过。其他 5 项尚无本轮生成的交接；旧日报缺少现行范围校验，入口显示未核实。

**安装路径和插件输出已核实；原生菜单的视觉与点击验收未完成。** 本轮原生界面检查只取得“SwiftBar 已在运行”的提示，关闭动作未改变可访问性树；未更改系统菜单栏设置。不能用脚本输出代替原生菜单已显示的证据。

## 使用

下面显式指定现用源码和正式数据目录，避免终端误用旧源码：

```sh
export PYTHONPATH=/Users/xiuqiu/.codex/worktrees/a45f/xiaomao-Agent/src
export XIAOMAO_HOME="$HOME/Library/Application Support/Xiaomao"
/Users/xiuqiu/.local/bin/python3.11 -m xiaomao --home "$XIAOMAO_HOME" latest handoff --project xiaomao-Agent --print
```

只读查询不重新采集。缺报告返回 2，陈旧或来源未核实返回 3；它们都不是程序通过。要主动更新这个项目的资料：

```sh
/Users/xiuqiu/.local/bin/python3.11 -m xiaomao --home "$XIAOMAO_HOME" scan --project xiaomao-Agent
/Users/xiuqiu/.local/bin/python3.11 -m xiaomao --home "$XIAOMAO_HOME" handoff --project xiaomao-Agent
```

生成时引用的采集截止超过 11 分钟后，读取会显示陈旧；后续后台扫描不会把旧报告引用时间自动改新。测试、部署及验收结果仍为 unknown。

## 验证边界与试运行

产品提交的最终隔离验收：107 项测试、6 个隔离验收门通过；`formal_home`、`volume_match` 两门 `NOT_RUN`，`full_live_acceptance=false`。另行完成的真实候选源码只读使用链路、正式安装入口和单轮正式扫描，仅证明各自范围，未运行正式完整验收或真实模型。

```sh
cd /Users/xiuqiu/.codex/worktrees/a45f/xiaomao-Agent
PYTHONPATH=src /Users/xiuqiu/.local/bin/python3.11 scripts/accept.py --isolated
```

- Pilot 开始：2026-09-21T00:58:55+00:00。
- 第 3 天核对：2026-09-24；第 7 天核对：2026-09-28。至少 3 个有真实开发输入的日期，否则保持 pending。
- 原始 Pilot 元数据、历史观察和模型摘要保留，没有重新起算。
- 当前没有 Siri / 语音、远程测试部署、跨设备同步、Web UI、MCP 或自动改仓能力。本轮未增加模型、下载模型或外发通道。
- SQLite 读取不修改应用行；可能创建 WAL / SHM 协调文件，不能声称整个数据目录零文件变化。

架构和维护方式见 [README.md](README.md)、[ARCHITECTURE.md](ARCHITECTURE.md)、[CONTRIBUTING.md](CONTRIBUTING.md)。跨任务证据与交付状态保存在 Token burn 的 `projects/xiaomao/`；项目自检状态为 READY_FOR_REVIEW，待总协调独立归集。

## 暂停与接续

现用源码下运行 `pause infer` 暂停可选推理，`pause scan` 停本系统 scan / daily 调度；对应 `resume` 恢复。不要删除历史 SQLite 或重置 Pilot 来解决显示问题，也不要恢复已经撤销的公司 / 旧仓配置。

需要迁移现用源码时，先验证新路径，再调整两个 plist 与插件链接，核对加载状态和一次真实扫描；保留当前依赖树直至新入口验证完成。原仓的未提交内容由负责人另行整合，不用 reset、clean 或覆盖文件做回退。
