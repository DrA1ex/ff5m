#!/bin/bash

## Update configuration file
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license


read_param() {
    local key="$1"
    local default="$2"
    
    if [ -z "$key" ]; then
        echo "Error: Empty key in --get mode"
        exit 1
    fi

    if [ ! -f "$CONFIG_FILE" ]; then
        echo "$default"
        return 0
    fi
    
    if grep -qE "^${key}\s*=\s*" "$CONFIG_FILE"; then
        value=$(sed -n -E "s/^$key\s*=\s*(.*)/\1/p" "$CONFIG_FILE")
        
        # Check if the value starts and ends with a single quote
        if [[ "$value" =~ ^\'.*\'$ ]]; then
            value="${value:1:-1}"
        fi

        echo "$value"
    else
        echo "$default"
    fi
}

update_param() {
    local key=$1
    local value=$2

    local existing=$(read_param "$key" "__NOT_EXISTS")

    if [ "$existing" == "__NOT_EXISTS" ]; then
        echo "Adding \"$key\" = \"$value\""
        echo "$key=$value" >> "$CONFIG_FILE"

    elif [ "$value" != "$existing" ]; then
        echo "Setting \"$key\" = \"$value\""
        sed -i -E "s|^($key\s*=\s*).*|\1$value|" "$CONFIG_FILE"
    fi
}

update_config() {
    cp -f "$CONFIG_FILE" "$CONFIG_FILE.bak"
    
    for arg in "$@"; do
        # Check if argument matches 'KEY=VALUE' pattern using grep
        if ! echo "$arg" | grep -qE '^[A-Za-z_][A-Za-z0-9_]*='; then
            echo "Warning: Invalid parameter assignment \"$arg\""
            continue
        fi

        key="${arg%%=*}"
        value="${arg#*=}"
        
        update_param "$key" "$value"
    done
    
    sync
}

usage() {
    echo "Usage: $0 (--get <key> [default] | --set <key=value ...>)"
}

CONFIG_FILE="$1"
shift

if [ ! -f "$CONFIG_FILE" ] && [ "$1" != "--get" ]; then
    echo "Error: File \"$CONFIG_FILE\" doesn't exists"
    usage
    exit 1
fi

case "$1" in
    --get)
        read_param "$2" "$3"
    ;;
    --set)
        shift
        update_config "$@"
    ;;
    *)
        usage
        exit 1
    ;;
esac

exit $?
