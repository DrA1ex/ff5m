#!/usr/bin/python
#
## FlashForge stock firmware API (HTTP and TCP)
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import json
import re
import socket
import subprocess
import sys


CONFIG_PATH = "/opt/config/Adventurer5M.json"
CURL_PATH = "/opt/cloud/curl-7.55.1-https/bin/curl"
HTTP_PORT = 8898
TCP_PORT = 8899


def printer_ip():
    for interface in ("wlan0", "eth0"):
        try:
            output = subprocess.check_output(
                ["ip", "addr", "show", interface], stderr=subprocess.DEVNULL
            ).decode("ascii", "ignore")
        except (OSError, subprocess.CalledProcessError):
            continue

        match = re.search(r"\binet\s+(\d{1,3}(?:\.\d{1,3}){3})", output)
        if match:
            return match.group(1)

    raise RuntimeError("No LAN IP address found (wlan0/eth0)")


def credentials():
    with open(CONFIG_PATH) as config_file:
        config = json.load(config_file)

    general = config["general"]

    serial = general.get("printerSerialNumber")
    access_code = general.get("lanCode")

    if not serial:
        raise RuntimeError("Printer serial number is missing")

    if access_code is None:
        raise RuntimeError("LAN access code is missing")

    return {
        "serialNumber": str(serial),
        "checkCode": str(access_code),
    }


def http_request(endpoint, payload):
    url = "http://{}:{}/{}".format(printer_ip(), HTTP_PORT, endpoint)
    command = [
        CURL_PATH, "--silent", "--show-error", "--fail",
        "--connect-timeout", "3", "--max-time", "12",
        url, "-H", "Content-Type: application/json",
        "--data", json.dumps(payload),
    ]
    result = subprocess.run(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    if result.returncode:
        raise RuntimeError("{} failed: {}".format(endpoint, result.stderr.strip()))

    try:
        response = json.loads(result.stdout)
    except ValueError:
        raise RuntimeError("{} returned invalid JSON".format(endpoint))

    if not isinstance(response, dict) or type(response.get("code")) is not int:
        raise RuntimeError("{} returned no valid result code".format(endpoint))
    if response["code"] != 0:
        raise RuntimeError("{} failed (code {}): {}".format(
            endpoint, response["code"], response.get("message", "Unknown error")
        ))

    print(result.stdout.strip())


def close_dialogs():
    payload = credentials()
    payload["payload"] = {
        "cmd": "stateCtrl_cmd", "args": {"action": "setClearPlatform"}
    }
    http_request("control", payload)


def print_file(filename):
    payload = credentials()
    payload.update({"fileName": filename, "levelingBeforePrint": True})
    http_request("printGcode", payload)


def send_command(command):
    # FlashForge's legacy TCP command interface is independent of LAN mode.
    message = "~{}\r\n".format(command).encode("utf-8")
    with socket.create_connection(("127.0.0.1", TCP_PORT), timeout=30) as conn:
        conn.sendall(message)
        response = conn.recv(1024)
    print("Response:", response.decode("utf-8", "replace"))


def select_file(filename):
    if "\r" in filename or "\n" in filename:
        raise ValueError("Invalid print filename")
    send_command("M23 0:/user/{}".format(filename))


def main(args):
    if args == ["close"]:
        close_dialogs()
    elif len(args) == 2 and args[0] == "print":
        print_file(args[1])
    elif len(args) == 2 and args[0] == "send":
        send_command(args[1])
    elif len(args) == 2 and args[0] == "send-file":
        select_file(args[1])
    else:
        print("Usage: flashforge_api.py close | print FILE | send CMD | send-file FILE",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except (OSError, ValueError, RuntimeError) as error:
        print("FlashForge API: {}".format(error), file=sys.stderr)
        sys.exit(1)
