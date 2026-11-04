#!/usr/bin/env bash
# The Indra sign-in link is only asked for when no saved session exists (or it has
# expired); put your account email under "indra" in config.local.json or set INDRA_EMAIL.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
"$SCRIPT_DIR/../venv/bin/python" "$SCRIPT_DIR/indradata.py" "$@"
