#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON_BIN=${PYTHON_BIN:-python3}
if [ -x "$SCRIPT_DIR/.venv/bin/python" ]; then
  PYTHON_BIN="$SCRIPT_DIR/.venv/bin/python"
fi

cd "$SCRIPT_DIR"
exec "$PYTHON_BIN" duck_web_ui.py "$@"
