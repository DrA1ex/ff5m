#!/bin/bash

## Synchronize changes, printer-side script
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license


PATH="/bin:/sbin:/usr/bin:/usr/sbin:/opt/bin:/opt/sbin:/opt/bin:/opt/sbin"


SKIP_RESTART=$1
SKIP_MOON_RESTART=$2
SKIP_KLIPPER_RESTART=$3
SKIP_MIGRATE=$4
SKIP_PLUGIN_RELOAD=$5
KLIPPER_HARD_RESTART=$6
REMOTE_DIR=$7
ARCHIVE_NAME=$8
VERBOSE=$9
FORCE_RESTART=${10}

COMMAND_TIMEOUT=15

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No color

cleanup() {
    if [ "$VERBOSE" -eq 1 ]; then echo "Cleanup: remove sync files..."; fi
    
    rm -f ./sync_*.tar*
    rm -rf "./.sync"
}

abort() {
    trap SIGINT
    echo; echo -e "${RED}Remote process aborted${NC}"
    cleanup
    
    exit 2
}

trap "abort" INT

print_status() {
    local name="$1"; local status="$2"; local color="$3"
    echo -e "${NC}► ${name}\t\t${color}${status}${NC}"
}

run_service() {
    if [ "$#" -lt 5 ]; then echo Missing required arguments; exit 3; fi
    
    local name="$1"; local status="$2"; local check_pid="$3"; local skip="$4"
    if [ "$check_pid" -eq 1 ]; then
        if [ "$#" -lt 7 ]; then echo Missing required arguments; exit 3; fi
        local pid_path="$5"; local invert="$6";
        local pid=$(cat "$pid_path" 2>/dev/null)
        shift 6; local command=("$@")
    else
        shift 4; local command=("$@")
    fi
    
    if [ "$skip" -eq 1 ]; then
        print_status "$name" "${status} skipped." "${BLUE}"
        return 0
    fi
    
    print_status "$name" "${status}..." "${YELLOW}"
    
    if [ "$VERBOSE" -eq 0 ]; then
        "${command[@]}" > /dev/null 2>&1
    else
        "${command[@]}"
    fi
    
    local ret=$?
    
    if [ "$ret" -ne 0 ]; then
        print_status "$name" "Failed" "${RED}"
        exit 2
    fi
    
    if [ "$check_pid" -eq 0 ]; then
        print_status "$name" "Done" "${GREEN}"
        return
    fi

    if [ -z "$pid" ]; then
        if [ "$invert" -eq 1 ]; then
            print_status "$name" "Done" "${GREEN}"
            return
        fi

        print_status "$name" "PID not found" "${RED}"
        exit 2
    fi
    
    for _ in $(seq 0 $COMMAND_TIMEOUT); do
        kill -0 "$pid" > /dev/null 2>&1; local ret=$?
        if [ $((ret == 0 ? !invert : invert)) -eq 1 ]; then
            print_status "$name" "Done" "${GREEN}"
            return
        fi
        sleep 1
    done
    
    print_status "$name" "Timeout" "${RED}"
    exit 2
}

cd "$REMOTE_DIR" || exit 1

rm -rf "./.sync"
mkdir "./.sync"

if [ $? -ne 0 ]; then
    echo -e "${RED}Failed to create sync directory${NC}"
    cleanup
    exit 1
fi

echo -e "${BLUE}Extracting archive...${NC}"

gzip -d "./${ARCHIVE_NAME}"

if [ $? -ne 0 ]; then
    echo -e "${RED}Failed to decompress sync archive${NC}"
    cleanup
    exit 1
fi

tar -xf "./${ARCHIVE_NAME%.*}" -C "./.sync/"

if [ $? -ne 0 ]; then
    echo -e "${RED}Failed to extract sync archive${NC}"
    cleanup
    exit 1
fi

echo -e "${BLUE}Comparing files...${NC}"

CHANGED=0
NETD_CHANGED=0
NETD_PID=""
EXPECTED_NETD="$(pwd)/mod/.bin/exec/netd"

# These package trees are fully managed by the project. Remove Python sources
# absent from the incoming archive before S00init reload cleans their matching
# Klipper extras symlinks.
for package in \
    ".py/klipper/plugins/ui" \
    ".py/klipper/plugins/ff5m_ui" \
    ".py/klipper/plugins/feather_ui_test"; do
    SRC_PACKAGE="./.sync/${package}"
    DEST_PACKAGE="./mod/${package}"
    if [ ! -d "$SRC_PACKAGE" ] || [ ! -d "$DEST_PACKAGE" ]; then
        continue
    fi
    while read -r dest_file; do
        relative="${dest_file#./mod/}"
        if [ -f "./.sync/${relative}" ]; then
            continue
        fi
        echo -e "${YELLOW}► Removing obsolete file: ${dest_file}${NC}"
        rm -f -- "$dest_file"
        if [ $? -ne 0 ]; then
            echo -e "${RED}Failed to remove obsolete file: ${dest_file}${NC}"
            cleanup
            exit 2
        fi
        CHANGED=1
    done < <(find "$DEST_PACKAGE" -type f -name "*.py")
done

while read -r file; do
    SRC_FILE="$file"
    DEST_FILE="./mod/${file#./.sync/}"
    
    if [ "$VERBOSE" -eq 1 ]; then echo "► Check: $SRC_FILE"; fi
    
    if ! cmp -s "$SRC_FILE" "$DEST_FILE"; then
        echo -e "${YELLOW}► File changed: $DEST_FILE${NC}"
        mkdir -p "${DEST_FILE%/*}"

        if [ $? -ne 0 ]; then
            echo -e "${RED}Failed to create directory: ${DEST_FILE%/*}${NC}"
            cleanup
            exit 2
        fi

        if [ "$DEST_FILE" = "./mod/.bin/exec/netd" ]; then
            # Replacing the inode is safe while the old daemon is executing;
            # truncating the running image in place fails with ETXTBSY.
            for candidate in $(pidof netd 2>/dev/null); do
                current_exe=$(readlink "/proc/$candidate/exe" 2>/dev/null)
                if [ "$current_exe" = "$EXPECTED_NETD" ]; then
                    NETD_PID="$candidate"
                    break
                fi
            done
            STAGED_FILE="${DEST_FILE}.sync.$$"
            rm -f "$STAGED_FILE"
            cp -p "$SRC_FILE" "$STAGED_FILE" \
                && mv -f "$STAGED_FILE" "$DEST_FILE"
            copy_result=$?
            rm -f "$STAGED_FILE"
            NETD_CHANGED=1
        else
            cp "$SRC_FILE" "$DEST_FILE"
            copy_result=$?
        fi

        if [ "$copy_result" -ne 0 ]; then
            echo -e "${RED}Failed to copy file: $DEST_FILE${NC}"
            cleanup
            exit 2
        fi

        CHANGED=1
    fi
done < <(find ./.sync -type f)

cleanup

if [ "$NETD_CHANGED" -eq 1 ] && [ -n "$NETD_PID" ]; then
    echo -e "${YELLOW}► Handing the live network to updated netd${NC}"
    if ! kill -HUP "$NETD_PID" 2>/dev/null; then
        echo -e "${RED}Failed to request netd handoff${NC}"
        exit 2
    fi
    for _ in 1 2 3 4 5; do
        kill -0 "$NETD_PID" 2>/dev/null || break
        sleep 1
    done
    if kill -0 "$NETD_PID" 2>/dev/null; then
        echo -e "${RED}Old netd did not stop after handoff${NC}"
        exit 2
    fi
    rm -f /run/netd.sock
    if ! start-stop-daemon -Sb --exec "$EXPECTED_NETD" -- --adopt-existing; then
        echo -e "${RED}Failed to start updated netd${NC}"
        exit 2
    fi
    for _ in 1 2 3 4 5; do
        [ -S /run/netd.sock ] && break
        sleep 1
    done
    if [ ! -S /run/netd.sock ]; then
        echo -e "${RED}Updated netd did not publish its socket${NC}"
        exit 2
    fi
fi

# To avoid restarting after Moonraker's Git repair.
SKIP_REBOOT_F="/data/.mod/.forge-x/tmp/mod_skip_reboot"

if [ "$CHANGED" -eq 1 ] && [ ! -f "$SKIP_REBOOT_F" ]; then
    echo -e "\n${YELLOW}Setup reboot skip for next forge-x update${NC}"
    touch "$SKIP_REBOOT_F"
fi

sync

if [ "$CHANGED" -eq 1 ]; then
    date -u +%Y-%m-%dT%H:%M:%SZ > /opt/config/mod/patch.txt
    cp -f /opt/config/mod/patch.txt /tmp/version_patch
    /opt/config/mod/.shell/motd.sh > /etc/motd
else
    echo; echo -e "${YELLOW}Printer is already in-sync${NC}"
fi

if [ "$CHANGED" -eq 1 ] || [ "$FORCE_RESTART" -eq 1 ] && [ "$SKIP_RESTART" -eq 0 ]; then
    echo; echo -e "${GREEN}Restarting services...${NC}\n"
    
    run_service "Moonraker" "Stopping"      1   "$SKIP_MOON_RESTART" \
    "/data/.mod/.forge-x/run/moonraker.pid"    1  /etc/init.d/S99root stop

    run_service "Database"  "Migrating"     0   "$SKIP_MIGRATE"           /opt/config/mod/.shell/migrate_db.sh
    run_service "Moonraker" "Starting"      0   "$SKIP_MOON_RESTART"      /etc/init.d/S99root start

    run_service "Plugins"   "Reloading"     0   "$SKIP_PLUGIN_RELOAD"     /etc/init.d/S00init reload
    
    if [ "$KLIPPER_HARD_RESTART" -ne 1 ]; then
        run_service "Klipper"   "Reloading"     0   "$SKIP_KLIPPER_RESTART"   /opt/config/mod/.shell/restart_klipper.sh
    else
        run_service "Klipper"   "Restarting"    0   "$SKIP_KLIPPER_RESTART"   /opt/config/mod/.shell/restart_klipper.sh --hard
    fi
    
    echo; echo -e "${GREEN}All done!${NC}"
fi
