#!/usr/bin/env bash
# The E.ON Next browser sign-in is only asked for when no saved session exists (or it can no
# longer be renewed); the session is kept in eonData/.session.json. EON_CALLBACK can supply the
# pasted callback URL.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
"$SCRIPT_DIR/../venv/bin/python" "$SCRIPT_DIR/eondata.py" "$@"
