#!/bin/bash
# Show what `make push` would change on Home Assistant, without changing it.
#
# Uses the same rsync options and exclude file as `make push`, in dry-run mode
# with --checksum so only real content differences are listed. Files that
# differ are fetched from HA into a temp dir and shown as a unified diff
# (HA's version on the left, local on the right). Files that exist only on HA
# are listed separately: `make push` would delete them.
#
# Usage: tools/ha_diff.sh <ha_host> <remote_path> <local_path> <exclude_file>

set -euo pipefail

ha_host="$1"
remote_path="${2%/}/"
local_path="${3%/}/"
exclude_file="$4"

# Never print these files' contents, only that they differ.
masked_re='(^|/)secrets\.yaml$'

itemized=$(rsync -ai -n --checksum --delete \
    --exclude-from="$exclude_file" --rsync-path="sudo rsync" \
    --out-format='%i %n' \
    "$local_path" "$ha_host:$remote_path")

changed=()
added=()
deleted=()
# Remote transfers are itemized with '<', local ones (as in tests) with '>'.
while read -r flags name; do
    [ -n "$flags" ] || continue
    case "$flags" in
        '*deleting') [[ "$name" == */ ]] || deleted+=("$name") ;;
        [\<\>]'f+++++++'*) added+=("$name") ;;
        [\<\>]'f'c*) changed+=("$name") ;;
    esac
done <<< "$itemized"

if [ ${#changed[@]} -eq 0 ] && [ ${#added[@]} -eq 0 ] && [ ${#deleted[@]} -eq 0 ]; then
    echo "Inga skillnader: $local_path och $ha_host:$remote_path är lika (för filer som make push rör)."
    exit 0
fi

if [ ${#changed[@]} -gt 0 ]; then
    tmp=$(mktemp -d)
    trap 'rm -rf "$tmp"' EXIT

    printf '%s\n' "${changed[@]}" |
        rsync -a --files-from=- --rsync-path="sudo rsync" \
            "$ha_host:$remote_path" "$tmp/"

    color=never
    [ -t 1 ] && color=always
    for f in "${changed[@]}"; do
        if [[ "$f" =~ $masked_re ]]; then
            echo "=== $f skiljer sig (innehållet visas inte)"
            continue
        fi
        diff -u --color="$color" \
            --label "HA:$f" --label "lokal:$f" \
            "$tmp/$f" "$local_path$f" || true
    done
    echo
fi

echo "Sammanfattning för make push:"
echo "  ändras på HA:  ${#changed[@]}"
for f in "${changed[@]}"; do echo "    ~ $f"; done
echo "  nya på HA:     ${#added[@]}"
for f in "${added[@]}"; do echo "    + $f"; done
echo "  raderas på HA: ${#deleted[@]}"
for f in "${deleted[@]}"; do echo "    - $f"; done

if [ ${#deleted[@]} -gt 0 ]; then
    echo
    echo "Filerna under 'raderas' finns bara på HA. Kör 'make pull' om de ska behållas."
fi
