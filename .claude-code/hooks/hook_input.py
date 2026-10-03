#!/usr/bin/env python3
"""Helpers for the Claude Code hooks; reads the tool call as JSON on stdin.

    hook_input.py file-path        print tool_input.file_path
    hook_input.py is-push HOST     exit 0 if the Bash command copies to HOST
                                   with rsync/scp, exit 10 if it does not;
                                   any other code (a crash) means "unknown"

Kept in Python so the hooks need nothing beyond the venv that validation
already requires (no jq).
"""

import json
import re
import shlex
import sys

COPY_COMMANDS = {"rsync", "scp"}
NOT_PUSH = 10
SEPARATORS = {";", "&", "&&", "|", "||", "\n"}


def segments(command):
    """Split a shell command into argv lists at ; & && | ||."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
    lexer.whitespace_split = True
    current = []
    for token in lexer:
        if token in SEPARATORS or set(token) <= set(";&|"):
            if current:
                yield current
            current = []
        else:
            current.append(token)
    if current:
        yield current


def is_push(command, host):
    """True if an rsync/scp in the command has HOST as a non-source path.

    A pull names the remote first (`rsync -a host:/config/ config/`); anything
    that names a local path before the remote is treated as a push. Options are
    skipped wherever they appear, so `... host:/config/ --delete` still counts.
    An option value such as `-e ssh` before a pull makes it look like a push,
    which only means validation runs once more than needed.
    """
    command = command.replace("\\\n", " ")
    remote = re.compile(r"^([^@/\s]+@)?" + re.escape(host) + r":")
    try:
        argvs = list(segments(command))
    except ValueError:
        # Unbalanced quotes: cannot tell the direction, so fail safe.
        return host in command and any(c in command for c in COPY_COMMANDS)

    for argv in argvs:
        names = [a.rsplit("/", 1)[-1] for a in argv]
        starts = [i for i, n in enumerate(names) if n in COPY_COMMANDS]
        if not starts:
            continue
        paths = [a for a in argv[starts[0] + 1 :] if not a.startswith("-")]
        if any(remote.match(p) for p in paths[1:]):
            return True
    return False


def main():
    data = json.load(sys.stdin)
    tool_input = data.get("tool_input") or {}
    mode = sys.argv[1]
    if mode == "file-path":
        print(tool_input.get("file_path") or "")
        return 0
    if mode == "is-push":
        command = tool_input.get("command") or ""
        return 0 if is_push(command, sys.argv[2]) else NOT_PUSH
    return 2


if __name__ == "__main__":
    sys.exit(main())
