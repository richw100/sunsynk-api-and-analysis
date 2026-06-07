#!/usr/bin/env bash

if [ -z "$SUNSYNK_USERNAME" ]; then
    read -rp "Enter Sunsynk Username: " SUNSYNK_USERNAME
    export SUNSYNK_USERNAME
fi

if [ -z "$SUNSYNK_PASSWORD" ]; then
    read -rsp "Enter Sunsynk Password: " SUNSYNK_PASSWORD
    echo
    export SUNSYNK_PASSWORD
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
"$SCRIPT_DIR/../venv/bin/python" "$SCRIPT_DIR/collectdata.py" "$@"
