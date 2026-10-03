#!/bin/bash
# PreToolUse hook: block a raw rsync/scp *to* the Home Assistant host unless
# the configuration validates.
#
# `make push` validates on its own, so it is not intercepted here. This hook
# covers the bypass: Claude copying files to HA directly. Exit 2 blocks the
# Bash call and shows stderr to Claude.

cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0

command=$(jq -r '.tool_input.command // empty')

case "$command" in
    *rsync* | *scp*) ;;
    *) exit 0 ;;
esac

ha_host=$(sed -n 's/^HA_HOST=//p' .env 2>/dev/null | tr -d "\"'" | tail -1)
[ -n "$ha_host" ] || exit 0

# A remote destination is the last argument of the command (or of the segment
# before ; && || |), written as [user@]host:path. A remote *source* (a pull)
# does not match because a local path follows it.
host_re=$(printf '%s' "$ha_host" | sed 's/[.[\*^$]/\\&/g')
if ! printf '%s\n' "$command" |
    grep -Eq "(rsync|scp)[^;&|]*[[:space:]]([^[:space:]]+@)?${host_re}:[^[:space:]]*[[:space:]]*($|[;&|])"; then
    exit 0
fi

if [ ! -f tools/run_tests.py ] || [ ! -x venv/bin/python ]; then
    echo "Blockerat: valideringsverktygen saknas, kör 'make setup' innan något kopieras till HA." >&2
    exit 2
fi

if ! output=$(venv/bin/python tools/run_tests.py 2>&1); then
    {
        echo "Blockerat: konfigurationen validerar inte, kopierar inte till $ha_host."
        echo "$output" | tail -60
    } >&2
    exit 2
fi

exit 0
