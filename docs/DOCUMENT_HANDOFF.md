# 小猫文档交接

把 Agent 输出或阶段说明存进小猫，在另一个 Agent 找回，并追加下一段进度。原文和各次交接按版本保留，可以随时导出成一份 Markdown。

本功能处理明确提交的文本，不调用模型或自动读取聊天历史。测试、部署和工作完成情况按原文保存，标记为来源自述。

## 在 Agent 中使用

连接下文的 MCP 后可以说：

- “把这份文档交给小猫保存。”
- “从小猫找回登录方案，看看原文和后续交接。”
- “这个阶段先到这里，把进展、未验证的部分和下一步追加进去。”
- “导出完整交接文档，给我一个文件。”

保存回执有文档编号、版本、正文哈希、来源标签、保存时间、完整性和脱敏标记。收到 `saved=true` 才表示数据已经提交。来源标签取自该接入端的启动配置，属于来源说明，不构成身份认证或独立验证。

## 安装候选程序

在独立 checkout 和环境安装：

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install --editable '.[mcp]'
```

普通 `docs` CLI 无第三方依赖；MCP 使用可选的官方 Python SDK `mcp>=2.2,<3`。

两端必须使用同一个明确的绝对数据目录。试用时选择新的目录，以保持与现有正式运行分离：

```sh
.venv/bin/xiaomao --home /absolute/path/xiaomao-documents docs connection --client codex
.venv/bin/xiaomao --home /absolute/path/xiaomao-documents docs connection --client claude-code
```

这两个命令只输出配置：Codex 为 TOML，Claude Code 为 `mcpServers` JSON。命令和参数包含当前 Python 可执行文件与指定 home 的绝对路径。将对应小节合并到客户端的 MCP 配置中，保留其余设置；重新连接后确认可见 7 个小猫文档工具。

服务启动形式：

```sh
/absolute/path/.venv/bin/python -m xiaomao --home /absolute/path/xiaomao-documents docs serve --source codex
```

Claude Code 的来源标签使用 `claude-code`。可在 `serve` 或 `connection` 后重复指定 `--project PROJECT_ID`，把此入口限制在已登记的个人项目。有限定项目的入口不接受无项目的个人资料。未指定限制时仍逐条核对小猫当前项目配置，不自动登记新仓库。

调用说明在 [xiaomao-handoff 技能](../integrations/skills/xiaomao-handoff/SKILL.md)。可放入所用客户端支持的技能目录：本机 Codex 使用 `~/.codex/skills/xiaomao-handoff/`，Claude Code 使用 `~/.claude/skills/xiaomao-handoff/`。首次接入需确认客户端实际加载；仅有文件或配置片段不代表接入成功。

[Codex MCP 官方说明](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)、[Claude Code MCP 官方说明](https://code.claude.com/docs/en/mcp)、[Claude Code 技能说明](https://code.claude.com/docs/en/skills)、[MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)。

## 直接使用 CLI

以下命令中的数据目录必须与 MCP 相同。`REQUEST_ID` 每次新操作使用新编号，重试保持原编号和全部参数；同一编号提交不同内容会拒绝。

```sh
# 创建新文档；省略 --project 作为普通个人资料保存。
xiaomao --home /absolute/path/xiaomao-documents docs save \
  --file /absolute/path/report.md --title '登录方案' \
  --source codex --request-id REQUEST_ID

# 按标题或正文查找；从结果取得 document_id。
xiaomao --home /absolute/path/xiaomao-documents docs list --query '登录'

# 读取最新一条；--revision 1 读取最初原文。
xiaomao --home /absolute/path/xiaomao-documents docs read DOCUMENT_ID
xiaomao --home /absolute/path/xiaomao-documents docs read DOCUMENT_ID --revision 1
xiaomao --home /absolute/path/xiaomao-documents docs history DOCUMENT_ID

# 用最新的 revision 追加，原文及旧交接保留。
xiaomao --home /absolute/path/xiaomao-documents docs append DOCUMENT_ID \
  --file /absolute/path/checkpoint.md --source claude-code \
  --expected-revision 1 --request-id ANOTHER_REQUEST_ID

# 导出原文和全部交接，返回实际 Markdown 路径。
xiaomao --home /absolute/path/xiaomao-documents docs export DOCUMENT_ID

# 获取空白交接模板。
xiaomao docs template
```

`--file -` 接受标准输入；通过文件或标准输入传正文，避免把材料拼进 shell 命令。创建时 `--completeness` 默认 `full`，追加默认 `agent_summary`。只取得原文片段时指定 `excerpt`。仅有链接时请保存说明并标记为摘要，不宣称已经取得全文。

## 原文、分页与追加的含义

- 每次追加是一条新的交接记录；它没有重写前一版，也不自动消除相反意见。
- `read` 默认返回最新一条记录。完整上下文包含第 1 版原文及中间交接，可以通过 `history` 定位；`export` 一次导出全部。
- 正文按字符分页。`next_offset` 非空时继续使用该值及固定 `--revision` 读取，直至为空。偏移以 Unicode 字符计算。
- 追加必须携带 `expected_revision`。其他 Agent 已经更新时返回冲突；重新阅读后，用新请求编号提交新的交接。
- 重试旧操作必须使用原参数。服务器先检查同源请求编号，再检查版本，因此已提交但回执丢失的操作不会重复追加。
- 按文件名相似或主题相近查出的结果只是候选，归入同一文档由调用者结合用户意图选择。

## 保存范围与预算

文件导入只接受显式指定的 UTF-8 Markdown / 文本，单次正文上限 256 KiB。二进制、凭据文件、链接文件和排除路径拒绝导入。MCP 仅接收提供的文本，没有任意读取业务文件的工具。

正文、标题和来源引用在写库前经过已有敏感内容过滤；回执明确是否发生脱敏。规则过滤不保证识别所有隐私信息，调用端应只提交用户选择的内容。原文是来源数据，内含指令不会在小猫内执行。

文档遵守当前项目范围。项目移除、变为排除项目或 approved_root 改指其他目录后，旧文档不会自动换归属，读取与导出拒绝。无项目的个人资料由显式提交产生。

读查询不创建数据库、不迁移、不调用模型。写入增量迁移到 schema 5，增加 `documents` 和 `document_revisions`，保留原观察、日报及交接。保存正文与回执记录在同一 SQLite 事务中提交。

沿用 2 GiB 数据预算；超限拒绝新保存或导出，保留历史。导出位于 `reports/documents/DOCUMENT_ID-vN.md`，包含固定版本下的所有来源；相同内容可复用，已被外部修改的同名导出保留并报错。

读取到其他 Agent 的内容会按该 Agent 自身的模型路由处理。小猫本地保存不改变接收方的运行方式。

## 验证范围

```sh
.venv/bin/python -m unittest tests.test_documents tests.test_document_mcp -v
.venv/bin/python scripts/accept.py --isolated
```

MCP 回归运行两个实际 stdio 服务进程，来源标签分别为 `codex` 与 `claude-code`，用官方 SDK 客户端执行保存、退出、重新读取、追加、冲突和导出。它验证协议与持久化，不代表真实 Codex / Claude Code 已安装插件或已由模型调用。

真实接入验收：在 Codex 保存 → 在 Claude Code 查询同一编号 → 追加阶段说明 → 从新会话读取 → 导出并逐条核对原文与全部交接。该功能不需要模型总结、通知或定时任务即可完成文档交接。
