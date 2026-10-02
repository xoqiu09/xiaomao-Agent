# 每日跨仓库总结升级

## 使用方式

默认窗口为 Asia/Taipei 前日 21:30 至本日 21:30，左闭右开。该日 21:30 及之后的修改进入次日报。生成前补采一次；正文固定为「今日总览 → 各项目变化 → 尚未提交的工作 → 采集缺口」。模型短句每项目最多 5 条；详细证据保留作者、完整 SHA、时间和工作树来源。

    xiaomao daily --date 2026-10-02
    xiaomao daily --with-model
    xiaomao latest daily --print
    xiaomao projects coverage --date 2026-10-02

--date 表示该日截止的窗口，默认今天。提前生成会标为预览，不提醒、不登记为完成。当天提交后 clean 的工作仍计入；没有新增变化的遗留 dirty 仅列为仍在进行。第一次观察只建基线。窗口之外的工作区状态不会倒灌旧日报。没有采集或提交证据的中间版本无法恢复。

## 配置与覆盖

旧配置继续使用已有登记，不因安装源码自动扩大范围。以下命令只修改所选 home 的配置；试用时在命令前加全局 --home /临时目录。

    xiaomao projects discover --root /Users/xiuqiu/PersonalProjects
    xiaomao projects configure --root /Users/xiuqiu/WorkSpace --root /Users/xiuqiu/PersonalProjects
    xiaomao projects configure --project xiaomao-Agent --worktrees all
    xiaomao projects configure --time 21:30 --timezone Asia/Taipei --notify on

- personal_roots：重复 --root 参数替换目录清单。发现最多 3 层、5000 个目录，不跟随目录软链接；更深目录需单独列为 root。
- personal_owners：默认 xoqiu09。只读 Git 身份元数据识别已确认个人仓库；不自动 fetch。GitHub SSH 别名和 HTTPS 地址会归一为同一仓库身份。
- worktree_policy：registered 只使用登记树；prefixes 使用已配置的 branch_prefixes；all 纳入普通分支与 detached 开发树。旧配置中的非空 branch_prefixes 保持兼容。命令切回 registered 会清空前缀。
- 同仓副本和工作树归到同一项目，提交按完整 SHA 去重。工作区修改仍分别标注来源目录，避免混淆不同开发状态。
- unknown candidate 只列目录，不读取代码。确认个人归属后可加入已有 projects 清单。排除规则继续作用于公司、第三方、归档、退役路径和 Xiaomao 的正式 release checkout。
- 目录无法访问、工作树消失、读取失败、首次基线、超出读取预算都会显示。更改范围后，新范围重新建立基线，旧范围报告保留但读取校验失效。

## 采集、解释与预算

每 5 分钟扫描一次。指纹包含允许读取文件的工作区及暂存区内容摘要，连续编辑同一 dirty 文件会产生新证据。差异覆盖提交、暂存、未暂存及新增文本；配置实值、凭据、二进制、软链接和超限内容不进入模型。

每文件约 2000 字符，每工作树或提交最多 200 个文件、64 KiB 差异片段；新增文件读取上限 32 KiB，跟踪文本 256 KiB。每轮每树最多补采 512 个提交，积压保留到下轮并注明。原 2 GiB 预算达到后停止保存新差异正文及模型解读，保留轻量状态和降级说明；不自动删除历史数据。

提交保留作者、作者时间、提交时间、完整 SHA 和合并父提交。观察到的分支/HEAD 变化会说明具体切换或同步操作未知；不把提交作者等同于本机操作者。修改与提交仅在内容指纹吻合时关联；观察到恢复会关联原修改，不以 clean 推断任务已完成。

正文、SwiftBar 短句及模型共享版本化证据包。每个有变化的项目独立调用已有本地模型，校验结构、证据引用和无依据的完成/部署声明。自然语言解释属于带来源的解读，技术证据仍由程序展示。项目证据超出模型输入预算、模型不可用、暂停或校验失败时生成规则日报。模型调用不持采集锁或 SQLite 写事务；不调用显式模型停止接口。

## 调度、补报与提醒

候选程序生成的 daily 调度描述保留晚间触发，增加启动及 300 秒检查。应用内按配置时区判断是否到截止时间，系统时区不同也不会提前发送完整日报。每次补齐最多 7 个已截止窗口，积压下轮继续。睡眠后的迟到提交可修订已生成窗口。

提醒按窗口先记账再发送。重复运行、补采或修订都不会重复提醒；系统拒绝通知或发送前进程中断时不会自动重试，以免重复。状态保存在 daily_notifications；仍可从菜单查看报告。

## 数据迁移与发布

schema 3 通过增量建表保留原观察、报告和扫描记录。新表包括 daily_trees、daily_samples、daily_commits、daily_commit_links、daily_checks、daily_windows、daily_model_cache 和 daily_notifications。报告元数据增加窗口、覆盖项目/工作树、证据版本及哈希。旧报告不覆盖为成功状态。

重新生成同日正文前，原正文和元数据会保存在 reports/daily/history/ 下，以内容哈希区分版本。历史目录不参与 latest 查询，不自动清理。

本次交付为候选源码及独立 PR，保留基线上的两笔提交，不合并。正式 release、plist、launchd、SwiftBar 链接及正式配置切换需另行发布。发布前备份原配置、调度描述、链接和 SQLite，使用新固定 checkout；恢复时应把旧程序与对应备份配置/数据库作为一组处理，保留新版数据供核对，不能通过删除历史记录解决问题。

正式启用后连续 3 个真实开发日，逐仓对账提交、未提交修改、覆盖树及缺口。隔离回归和样例通过不代表该真实验收已完成。

## 隔离验收

在独立 worktree 中建立独立环境：

    python3.11 -m venv .venv
    .venv/bin/python -m pip install --no-deps --editable .
    .venv/bin/python -m unittest discover -s tests
    .venv/bin/python scripts/accept.py --isolated
    .venv/bin/python -m tests.daily_scenario --output /tmp/xiaomao-daily-example

模型测试使用替身；通知测试使用假发送器。运行前检查进程控制动作；不停止真实模型或系统任务。样例预先标注 3 个项目、5 棵工作树、1 个去重提交、2 棵遗留/新增未提交树和 1 个候选，分别生成正文、证据包及对账结果。正式 home、外盘身份和真实模型/通知仍单独验收。
