#!/bin/bash

## Synchronize local changes to the printer
##
## Copyright (C) 2025, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No color


SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" || exit 1
cd "$SCRIPT_DIR" || exit 1

REMOTE_HOST=""
REMOTE_USER="root"
REMOTE_DIR="/opt/config/"
ARCHIVE_NAME="sync_$(date +%Y%m%d_%H%M%S)_$$.tar.gz"

SKIP_HEAVY=0

SKIP_RESTART=0
FORCE_RESTART=0
SKIP_MOON_RESTART=0
SKIP_KLIPPER_RESTART=0
SKIP_MIGRATE=0
SKIP_PLUGIN_RELOAD=0

KLIPPER_HARD_RESTART=0

HELP=0
VERBOSE=0

PROFILES_KEYS=("all" "light" "none" "macros" "config" "scripts" "klipper" "moonraker")
PROFILES_VALUES=(   
    ""
    "
        SKIP_HEAVY=1
        SKIP_RESTART=1
    "
    "
        SKIP_RESTART=1
    "
    "
        SKIP_MOON_RESTART=1
        SKIP_MIGRATE=1
        SKIP_PLUGIN_RELOAD=1
        SKIP_HEAVY=1
    "
    "
        SKIP_RESTART=0
        SKIP_MOON_RESTART=1
        SKIP_PLUGIN_RELOAD=1
        SKIP_MIGRATE=1
        SKIP_HEAVY=1
    "
    "
        SKIP_MOON_RESTART=1
        SKIP_KLIPPER_RESTART=1
        SKIP_MIGRATE=1
        SKIP_HEAVY=1
    "
    "
        SKIP_MOON_RESTART=1
        SKIP_MIGRATE=1
        SKIP_PLUGIN_RELOAD=0
        KLIPPER_HARD_RESTART=1
        SKIP_HEAVY=1
    "
    "
        SKIP_KLIPPER_RESTART=1
        SKIP_MIGRATE=1
        SKIP_PLUGIN_RELOAD=1
    "
)

print_label() {
    if [ $# -eq 2 ]; then
        echo -e "${BLUE}[$1] $2${NC}"
    else
        echo -e "${BLUE}$1${NC}"
    fi
}

usage() {
    echo
    echo -e "${RED}Usage:${NC} $0 --host <printer_ip> [options]"
    echo -e ""
    echo -e "${BLUE}Options:${NC}"
    echo -e "    --host, -h               Specify the remote printer's IP address."
    echo -e "    --profile, -p            Specify profile with preconfigured parameters."
    echo -e "                             (all|none|macros|config|scripts|klipper|moonraker)"
    echo -e "    --skip-heavy, -sh        Skip transferring heavy files."
    echo -e "    --skip-restart, -sr      Skip restarting the services."
    echo -e "    --skip-database          Skip database migration."
    echo -e "    --skip-moon-restart      Skip restarting Moonraker."
    echo -e "    --skip-klipper-restart   Skip restarting Klipper."
    echo -e "    --skip-plugins           Skip Klipper plugins reloading."
    echo -e "    --hard-klipper-restart   Use Hard restart for Klipper."
    echo -e "    --force-restart, -fr     Force restart services."
    echo -e "    --verbose, -v            Enable verbose mode for detailed output."
    echo -e "    --help                   Display this help message."
    echo -e ""
    echo -e "${RED}Example:${NC} $0 --host 192.168.1.100 --skip-restart --verbose"
}

load_profile() {
    local key=$1

    for i in "${!PROFILES_KEYS[@]}"; do
        if [[ "${PROFILES_KEYS[i]}" == "$key" ]]; then
            while IFS= read -r line; do
                line=$(echo "$line" | xargs)
                [ -z "$line" ] && continue
                
                eval "$line"
            done <<< "${PROFILES_VALUES[i]}"

            PROFILE="$key"
            return 0
        fi
    done

    echo -e "${RED}Unknown profile: \"${key}\"${NC}\n"
    HELP=1

    return 1
}

echo -e "${GREEN}Flashforge Forge-X synchronization script${NC}\n"

while [ "$#" -gt 0 ]; do
    param=$1; shift
    case $param in
        --host|-h)
            if [ -z "$1" ] || [[ "$1" == -* ]]; then
                echo -e "${RED}Missing value for ${param}.${NC}"
                HELP=1
                break
            fi

            REMOTE_HOST="$1"; shift
            print_label "!" "Remote host: ${REMOTE_HOST}."
        ;;
        --profile|-p)
            if [ -z "$1" ] || [[ "$1" == -* ]]; then
                echo -e "${RED}Missing value for ${param}.${NC}"
                HELP=1
                break
            fi

            load_profile "$1"; shift
        ;;
        --skip-restart|-sr)
            SKIP_RESTART=1
            FORCE_RESTART=0
        ;;
        --skip-moon-restart)
            SKIP_MOON_RESTART=1
        ;;
        --skip-klipper-restart)
            SKIP_KLIPPER_RESTART=1
        ;;
        --skip-database)
            SKIP_MIGRATE=1
        ;;
        --skip-heavy|-sh)
            SKIP_HEAVY=1
        ;;
        --skip-plugins)
            SKIP_PLUGIN_RELOAD=1
        ;;
        --hard-klipper-restart)
            if [ "$SKIP_RESTART" -eq 0 ]; then
                KLIPPER_HARD_RESTART=1
            fi
        ;;
        --force-restart|-fr)
            SKIP_RESTART=0
            FORCE_RESTART=1
        ;;
        --verbose|-v)
            print_label "*" "Verbose mode enabled."
            VERBOSE=1
        ;;
        --help)
            HELP=1
        ;;
        *)
            HELP=1
            echo -e "${RED}Unknown parameter: \"${param}\"${NC}"
        ;;
    esac
done

if [ -n "$PROFILE" ]; then
    print_label "!" "Selected profile: ${PROFILE}"
fi

if [ "$SKIP_RESTART" -eq 1 ]; then
    print_label "-" "Services restart will be skipped."
fi

if [ "$SKIP_MOON_RESTART" -eq 1 ]; then
    print_label "-" "Moonraker restart will be skipped."
fi

if [ "$SKIP_KLIPPER_RESTART" -eq 1 ]; then
    print_label "-" "Klipper restart will be skipped."
fi

if [ "$SKIP_MIGRATE" -eq 1 ]; then
    print_label "-" "Database migration will be skipped."
fi

if [ "$SKIP_HEAVY" -eq 1 ]; then
    print_label "-" "Heavy files will be skipped."
fi

if [ "$SKIP_PLUGIN_RELOAD" -eq 1 ]; then
    print_label "-" "Plugin reloading will be skipped."
fi

if [ "$KLIPPER_HARD_RESTART" -eq 1 ]; then
    print_label "+" "Klipper hard restart mode enabled."
fi

if [ "$FORCE_RESTART" -eq 1 ]; then
    print_label "+" "Force services restart."
fi

if [ "$HELP" = 1 ] || [ -z "$REMOTE_HOST" ]; then
    usage
    exit 1
fi


cleanup() {
    rm -f "./${ARCHIVE_NAME}"
}

abort() {
    trap SIGINT
    echo; echo -e "${RED}Process aborted${NC}"
    cleanup
    
    exit 2
}

trap "abort" INT

declare -a EXCLUDES=(
    "./${ARCHIVE_NAME}"
    ".git"
    ".idea"
    ".vscode"
    ".DS_Store"
    "./sync*.tar.gz"
    "./sync.sh"
    "*/__pycache__"
    "./sync_remote.sh"
    "./tests/"
    "./.bin/src/**/bin"
    "./.bin/src/**/CMakeFiles"
    "./.bin/src/**/Makefile"
    "./.bin/src/**/CMakeCache.txt"
    "./.bin/src/**/cmake_install.cmake"
    "./.bin/src/**/.cmake"
    "./.bin/src/**/Testing"
    "./.bin/src/cmake-build-*"
)

if [ "$SKIP_HEAVY" -eq 1 ]; then
    EXCLUDES+=(
        "./.root/docs/"
        "./.root/config/"
        "./.root/klippy/"
        "./.root/moonraker/"
        "./.zsh/.oh-my-zsh/"
        "./.bin/"
    )
fi

echo
print_label "Creating archive..."

EXCLUDE_ARGS=()
for e in "${EXCLUDES[@]}"; do
    EXCLUDE_ARGS+=(--exclude="$e")
    if [ "$VERBOSE" -eq 1 ]; then echo "► Excluding: \"$e\""; fi
done

tar "${EXCLUDE_ARGS[@]}" --disable-copyfile -czf "${ARCHIVE_NAME}" .
if [ $? -ne 0 ]; then
    echo -e "\n${RED}Unable to create sync archive.${NC}"
    
    cleanup
    exit 2
fi

print_label "Uploading archive to ${REMOTE_HOST}..."
scp -O "./${ARCHIVE_NAME}" "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}"

if [ $? -ne 0 ]; then
    echo -e "\n${RED}Unable to upload sync archive to the printer at ${REMOTE_HOST}.${NC}"
    echo "Make sure you have added identity to the printer:"
    echo "ssh-copy-id -i \"/path/to/ssh_key.pub\" root@${REMOTE_HOST}"
    
    cleanup
    exit 2
fi

echo

ssh "${REMOTE_USER}@${REMOTE_HOST}" 'bash -s ' < sync_remote.sh     \
    "$SKIP_RESTART" "$SKIP_MOON_RESTART" "$SKIP_KLIPPER_RESTART"    \
    "$SKIP_MIGRATE" "$SKIP_PLUGIN_RELOAD" "$KLIPPER_HARD_RESTART"   \
    "$REMOTE_DIR" "$ARCHIVE_NAME" "$VERBOSE" "$FORCE_RESTART"

ret=$?

cleanup

if [ "$ret" -ne 0 ]; then
    echo -e "\n${RED}Unable to sync files to the printer at ${REMOTE_HOST}.${NC}"
    exit 2
fi

echo; echo -e "${GREEN}Sync complete!${NC}"
