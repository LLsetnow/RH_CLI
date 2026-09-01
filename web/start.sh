#!/bin/sh
set -eu

WEB_APP_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON_BIN="$WEB_APP_ROOT/.venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN=python3
fi

PYTHONPATH="$WEB_APP_ROOT/src${PYTHONPATH:+:$PYTHONPATH}" \
  exec "$PYTHON_BIN" -m web.server "$@"
