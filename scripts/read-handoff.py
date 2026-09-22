#!/usr/bin/env python3
"""SwiftBar action: arguments are encoded data, never a shell command."""
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from xiaomao.cli import main

if __name__ == "__main__":
    home, project = json.loads(base64.urlsafe_b64decode(sys.argv[1]))
    if not isinstance(home, str) or not isinstance(project, str):
        raise SystemExit(2)
    raise SystemExit(main(["--home", home, "latest", "handoff", "--project", project, "--print"]))
