#!/bin/bash
# PostToolUse hook: validate the Home Assistant configuration after Claude
# edits a YAML file under config/.
#
# Claude Code passes the tool call as JSON on stdin. Exit 2 sends stderr back
# to Claude so it sees the validation errors and can fix them; the edit itself
# has already happened and is not undone.

cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0

python=venv/bin/python
[ -x "$python" ] || python=python3
file_path=$("$python" .claude-code/hooks/hook_input.py file-path)

case "$file_path" in
    */config/*.yaml | */config/*.yml | config/*.yaml | config/*.yml) ;;
    *) exit 0 ;;
esac

if [ ! -f tools/run_tests.py ] || [ ! -x venv/bin/python ]; then
    echo "HA-validering hoppades över: kör 'make setup' först." >&2
    exit 0
fi

if ! output=$(venv/bin/python tools/run_tests.py 2>&1); then
    {
        echo "Home Assistant-valideringen misslyckades efter ändring i $file_path:"
        echo "$output" | tail -60
    } >&2
    exit 2
fi

exit 0
