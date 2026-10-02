#!/usr/bin/env bash
# Phone number / SMS code are prompted for by the script only when no saved
# session exists (or it has expired); set FUSE_PHONE to skip the phone prompt.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
"$SCRIPT_DIR/../venv/bin/python" "$SCRIPT_DIR/fusedata.py" "$@"
