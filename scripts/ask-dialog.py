#!/usr/bin/env python3
"""SwiftBar action「问小猫…」: a dialog in, a dialog out.

The question and the answer reach AppleScript only as argv data, never as
script text. The answer comes from `xiaomao ask` and keeps its boundaries.
"""
import base64
import io
import json
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

OSASCRIPT = "/usr/bin/osascript"
ASK = ('on run argv\n set r to display dialog (item 1 of argv) default answer "" with title "问小猫" '
       'buttons {"取消", "问"} default button "问" cancel button "取消"\n return text returned of r\nend run')
SHOW = ('on run argv\n set r to display dialog (item 1 of argv) with title "小猫的回答" '
        'buttons {"复制", "好"} default button "好"\n return button returned of r\nend run')
NOTE = 'on run argv\n display notification (item 1 of argv) with title "问小猫"\nend run'
PROMPT = "想问小猫什么？\n项目问题按小猫的记录回答；没把握的会建议你去问 Codex 或 Claude。"
MAX_QUESTION = 500
MAX_SHOWN = 1800


def osascript(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([OSASCRIPT, "-e", script, *args], capture_output=True, text=True, timeout=600)


def main(argv: list[str]) -> int:
    from xiaomao.ask import ask_command
    home = json.loads(base64.urlsafe_b64decode(argv[1]))[0]
    if not isinstance(home, str):
        return 2
    asked = osascript(ASK, PROMPT)
    question = asked.stdout.strip()[:MAX_QUESTION]
    if asked.returncode or not question:
        return 0
    osascript(NOTE, "小猫在查记录，大约 1 分钟")
    out, err = io.StringIO(), io.StringIO()
    code = ask_command(Namespace(question=question, days=None, project=None, model=None), Path(home),
                       out=out, err=err)
    text = (out.getvalue() or err.getvalue()).strip() or "没有得到回答。"
    if osascript(SHOW, text[:MAX_SHOWN]).stdout.strip() == "复制":
        subprocess.run(["/usr/bin/pbcopy"], input=text, text=True, timeout=10, check=False)
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
