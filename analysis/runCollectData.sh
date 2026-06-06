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

python3 "$(dirname "$0")/collectdata.py" Off 5000 on 2500 5000
