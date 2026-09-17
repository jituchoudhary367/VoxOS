#!/usr/bin/env bash
# Runs the given experiments one after another, detached from the calling shell.
# Usage: bash run_queue.sh <log> <wait-for-marker-file-or-empty> exp_a exp_b ...
cd "$(dirname "$0")"
export PYTHONIOENCODING=utf-8
log="results/$1"; shift
wait_marker="$1"; shift
if [ -n "$wait_marker" ] && [ "$wait_marker" != "none" ]; then
  until grep -q ALLDONE "results/$wait_marker" 2>/dev/null; do sleep 10; done
fi
for e in "$@"; do
  echo "=== START $e $(date +%T)" >> "$log"
  taskkill //IM notepad.exe //F >/dev/null 2>&1
  python -u "$e.py" 2>&1 | grep --line-buffered -v '^{"event\|^20[0-9][0-9]-[0-9][0-9]-' >> "$log"
  echo "=== END $e $(date +%T)" >> "$log"
done
echo ALLDONE >> "$log"
