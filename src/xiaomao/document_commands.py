"""Document handoff commands; all capture is explicit."""
import json
import sys
from pathlib import Path

from xiaomao.documents import DocumentStore, MAX_DOCUMENT_BYTES, read_document_file
from xiaomao.paths import default_home

TEMPLATE = """# 阶段交接

## 当前目标

## 关键结论与条件

## 已完成的工作

## 验证情况与证据
分别写明实际验证、来源自述、尚未验证。

## 未决问题与阻塞

## 下一步

## 文档、代码、提交与会话来源

## 不应被覆盖的已有工作
"""


def command(ns):
    home = Path(ns.home).expanduser().resolve() if ns.home else default_home()
    store = DocumentStore(home)
    action = ns.docs_action
    if action == "serve":
        from xiaomao.document_mcp import serve
        serve(home, source=ns.source, allowed_projects=tuple(ns.project) if ns.project else None)
        return 0
    if action == "template":
        print(TEMPLATE)
        return 0
    if action == "connection":
        args = ["-m", "xiaomao", "--home", str(home), "docs", "serve", "--source", ns.client]
        for project in ns.project or []:
            args.extend(["--project", project])
        connection = {"command": sys.executable, "args": args}
        if ns.client == "codex":
            print('[mcp_servers.xiaomao-documents]')
            print("command = " + json.dumps(sys.executable, ensure_ascii=False))
            print("args = " + json.dumps(args, ensure_ascii=False))
        else:
            print(json.dumps({"mcpServers": {"xiaomao-documents": connection}}, ensure_ascii=False, indent=2))
        return 0
    if action in {"save", "append"}:
        if ns.file == "-":
            raw = sys.stdin.buffer.read(MAX_DOCUMENT_BYTES + 1)
            if len(raw) > MAX_DOCUMENT_BYTES:
                raise ValueError("正文超过读取上限")
            try:
                content = raw.decode("utf-8")
            except UnicodeDecodeError:
                raise ValueError("正文必须是 UTF-8 文本") from None
        else:
            content = read_document_file(Path(ns.file))
        args = dict(content=content, source=ns.source, request_id=ns.request_id,
                    source_ref=ns.source_ref or (str(Path(ns.file).expanduser().absolute()) if ns.file != "-" else ""),
                    completeness=ns.completeness)
        if action == "save":
            result = store.save(title=ns.title, project_id=ns.project, **args)
        else:
            result = store.append(ns.document_id, expected_revision=ns.expected_revision, **args)
    elif action == "list":
        result = store.list(project_id=ns.project, query=ns.query, offset=ns.offset, limit=ns.limit)
    elif action == "read":
        result = store.read(ns.document_id, revision=ns.revision, offset=ns.offset, max_chars=ns.max_chars)
    elif action == "history":
        result = store.history(ns.document_id, offset=ns.offset, limit=ns.limit)
    else:
        result = store.export(ns.document_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def add_parser(sub):
    docs = sub.add_parser("docs", help="显式保存文档、追加交接、查询版本或导出 Markdown")
    actions = docs.add_subparsers(dest="docs_action", required=True)
    save = actions.add_parser("save", help="保存一份新文档，成功返回持久化回执")
    save.add_argument("--title", required=True)
    save.add_argument("--project", help="可选的已登记个人项目；省略为个人资料")
    append = actions.add_parser("append", help="为已有文档追加阶段说明，保留所有历史")
    append.add_argument("document_id")
    append.add_argument("--expected-revision", type=int, required=True)
    for parser in (save, append):
        parser.add_argument("--file", required=True, help="UTF-8 文档；- 表示从标准输入读取")
        parser.add_argument("--source", required=True, help="来源标签，例如 codex、claude-code 或 manual")
        parser.add_argument("--source-ref", default="", help="已有会话/来源引用；不会自动访问")
        parser.add_argument("--request-id", required=True, help="每次保存生成一个编号；重试沿用同一编号")
        parser.add_argument("--completeness", choices=["full", "excerpt", "agent_summary"],
                            default="full" if parser is save else "agent_summary")
    listing = actions.add_parser("list", help="按项目或正文关键词查找文档")
    listing.add_argument("--project")
    listing.add_argument("--query", default="")
    history = actions.add_parser("history", help="列出版本与来源")
    history.add_argument("document_id")
    for parser in (listing, history):
        parser.add_argument("--offset", type=int, default=0)
        parser.add_argument("--limit", type=int, default=20)
    read = actions.add_parser("read", help="分页读取最新交接；--revision 1 读取原始文档")
    read.add_argument("document_id")
    read.add_argument("--revision", type=int)
    read.add_argument("--offset", type=int, default=0)
    read.add_argument("--max-chars", type=int, default=12000)
    export = actions.add_parser("export", help="导出包含原文及全部交接的固定版本 Markdown")
    export.add_argument("document_id")
    actions.add_parser("template", help="输出可填写的交接模板")
    connection = actions.add_parser("connection", help="输出两端接入配置；不安装或覆盖已有设置")
    connection.add_argument("--client", choices=["codex", "claude-code"], required=True)
    connection.add_argument("--project", action="append")
    server = actions.add_parser("serve", help="启动本地 stdio MCP 文档工具（需要 mcp 可选依赖）")
    server.add_argument("--source", required=True, help="该接入端的来源标签")
    server.add_argument("--project", action="append", help="将服务限定在指定项目，可重复")
    docs.set_defaults(func=command)
