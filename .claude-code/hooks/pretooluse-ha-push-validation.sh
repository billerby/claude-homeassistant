#!/bin/bash
# PreToolUse hook: block a raw rsync/scp *to* the Home Assistant host unless
# the configuration validates.
#
# `make push` validates on its own, so it is not intercepted here. This hook
# covers the bypass: Claude copying files to HA directly. Exit 2 blocks the
# Bash call and shows stderr to Claude.

cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0

input=$(cat)

case "$input" in
    *rsync* | *scp*) ;;
    *) exit 0 ;;
esac

ha_host=$(sed -n 's/^HA_HOST=//p' .env 2>/dev/null | tr -d "\"'" | tail -1)
[ -n "$ha_host" ] || exit 0

python=venv/bin/python
[ -x "$python" ] || python=python3

printf '%s' "$input" | "$python" .claude-code/hooks/hook_input.py is-push "$ha_host"
case $? in
    0) ;;
    10) exit 0 ;;
    *)
        echo "Blockerat: kunde inte avgöra om kommandot kopierar till $ha_host (hook_input.py fallerade)." >&2
        exit 2
        ;;
esac

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
