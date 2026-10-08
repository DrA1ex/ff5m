#!/bin/bash

# Bridge to the stock FlashForge LAN API.
# Tests can override the device paths and curl executable via ZPRINT_*.

if [ "$#" -ne 2 ]; then
    echo "Usage: $0 PRINT|CLOSE FILE" >&2
    exit 1
fi

CURL="${ZPRINT_CURL:-/opt/cloud/curl-7.55.1-https/bin/curl}"
CONFIG="${ZPRINT_CONFIG_FILE:-/opt/config/Adventurer5M.json}"
DATA_DIR="${ZPRINT_DATA_DIR:-/data}"
GCODE_INPUT="${ZPRINT_GCODE_INPUT:-/tmp/printer}"

ip="${ZPRINT_IP:-$(ip -4 addr show wlan0 2>/dev/null | awk '/inet / {sub(/\/.*/, "", $2); print $2; exit}')}"
if [ -z "$ip" ]; then
    ip=$(ip -4 addr show eth0 2>/dev/null | awk '/inet / {sub(/\/.*/, "", $2); print $2; exit}')
fi

credentials=$(/usr/bin/python -c '
import json, sys
with open(sys.argv[1]) as f:
    config = json.load(f)
sys.stdout.write(str(config.get("printerSerialNumber") or "") + "\n")
sys.stdout.write(str(config.get("lanCode") or "") + "\n")
' "$CONFIG") || { echo "FlashForge LAN API: unable to read $CONFIG" >&2; exit 1; }
serialNumber=${credentials%%$'\n'*}
checkCode=${credentials#*$'\n'}
if [ -z "$ip" ] || [ -z "$serialNumber" ] || [ -z "$checkCode" ]; then
    echo "FlashForge LAN API: missing printer IP, serial number, or LAN code (check LAN mode and $CONFIG)" >&2
    exit 1
fi

post_json() {
    local endpoint="$1" payload="$2" response
    if ! response=$("$CURL" -sS --fail --connect-timeout 3 --max-time 12 \
            "http://$ip:8898/$endpoint" -H 'Content-Type: application/json' -d "$payload"); then
        echo "FlashForge LAN API /$endpoint: HTTP/connection error (check LAN mode)" >&2
        return 1
    fi

    printf '%s\n' "$response" | /usr/bin/python -c '
import json, sys
try:
    data = json.load(sys.stdin)
except (ValueError, TypeError):
    sys.stderr.write("FlashForge LAN API: invalid JSON response\n")
    sys.exit(1)
if not isinstance(data, dict) or type(data.get("code")) is not int:
    sys.stderr.write("FlashForge LAN API: missing or invalid response code\n")
    sys.exit(1)
if data["code"] != 0:
    sys.stderr.write("FlashForge LAN API: code %s (%s)\n" %
                     (data["code"], data.get("message", "unknown error")))
    sys.exit(1)
sys.stdout.write(json.dumps(data) + "\n")
'
}

case "$1" in
    CLOSE)
        endpoint=control
        ;;
    PRINT)
        endpoint=printGcode
        file="$DATA_DIR/$2"
        if [ ! -f "$file" ]; then
            echo "Print file does not exist: $file" >&2
            exit 1
        fi

        echo 'EXCLUDE_OBJECT_DEFINE RESET=1' >"$GCODE_INPUT" 2>/dev/null
        head -n 1000 "$file" | grep '^EXCLUDE_OBJECT_DEFINE' >"$GCODE_INPUT" 2>/dev/null

        M109=$(head -n 1000 "$file" | grep '^M109' | head -1)
        [ -z "$M109" ] && M109=$(head -n 1000 "$file" | grep '^M104' | head -1)
        M190=$(head -n 1000 "$file" | grep '^M190' | head -1)
        [ -z "$M190" ] && M190=$(head -n 1000 "$file" | grep '^M140' | head -1)
        if [ -z "$M190" ] || [ -z "$M109" ]; then
            echo "RESPOND TYPE=error MSG=\"The file $2 does not contain bed (M140/M190) or nozzle (M104/M109) heating commands.\"" >"$GCODE_INPUT"
            exit 1
        fi
        ;;
    *)
        echo "Usage: $0 PRINT|CLOSE FILE" >&2
        exit 1
        ;;
esac

payload=$(/usr/bin/python -c '
import json, sys
serial, code, operation, filename = sys.argv[1:]
request = {"serialNumber": serial, "checkCode": code}
if operation == "CLOSE":
    request["payload"] = {"cmd": "stateCtrl_cmd", "args": {"action": "setClearPlatform"}}
else:
    request.update({"fileName": filename, "levelingBeforePrint": True})
sys.stdout.write(json.dumps(request))
' "$serialNumber" "$checkCode" "$1" "$2") || exit 1

post_json "$endpoint" "$payload"
