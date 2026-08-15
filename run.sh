#!/bin/bash
# One command for your weekly routine: lineup email, then FAAB recap.
# Usage:
#   ./run.sh              # sends both emails for real
#   ./run.sh --dry-run     # prints both instead of sending (safe to preview)
set -e
cd "$(dirname "$0")"

echo "=== Weekly Lineup ==="
.venv/bin/python scripts/weekly_lineup.py "$@"

echo
echo "=== Recap / FAAB Targets ==="
.venv/bin/python scripts/recap.py "$@"

echo
echo "Done. (Sunday-afternoon inactives check is optional -- run"
echo "'.venv/bin/python scripts/inactives_check.py' again later the same day if you want it.)"
