#!/usr/bin/env bash
# Runs the unit tests of every project that has them. Exits non-zero if any fail.
set -uo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
status=0
for dir in "$root"/projects/*/; do
  if ls "$dir"test*.py >/dev/null 2>&1; then
    echo "== $(basename "$dir") =="
    (cd "$dir" && python3 -m unittest discover -v) || status=1
  fi
done
exit $status
