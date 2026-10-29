#!/usr/bin/env bash
# Start the app:  bash start.sh   (stop with Control + C)
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "Not set up yet. Run first:  bash setup.sh"
  exit 1
fi
exec .venv/bin/python run.py
