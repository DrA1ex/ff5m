#!/usr/bin/env python3
# Show or hide the Forge-X camera and screen entries in the web interface.
#
# The entries are created by an SQL migration. This client only sets their
# `enabled` flag through the Moonraker API and leaves every other field to the user.
#
# Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
#
# This file may be distributed under the terms of the GNU GPLv3 license

import argparse
import json
import time
from urllib.request import Request, urlopen

ENTRIES = {'camera': '4gx_camera', 'screen': '4gx_screen'}


def api_request(method, endpoint, data=None):
    payload = None if data is None else json.dumps(data).encode()
    request = Request('http://127.0.0.1:7125' + endpoint, data=payload,
                      headers={'Content-Type': 'application/json'}, method=method)
    with urlopen(request, timeout=3) as response:
        return json.load(response)['result']


def list_webcams(request, wait):
    deadline = time.monotonic() + wait
    while True:
        try:
            return request('GET', '/server/webcams/list')['webcams']
        except OSError:
            # The API may still be starting.
            if time.monotonic() >= deadline:
                raise
            time.sleep(1)


def set_enabled(request, wanted, wait=0):
    """Update only entries whose flag differs; a missing or config-defined entry is the user's choice."""
    webcams = {cam['name']: cam for cam in list_webcams(request, wait)}
    for key, name in ENTRIES.items():
        cam = webcams.get(name)
        if cam is None:
            print(f'{name} is not registered; skipping')
        elif cam['source'] != 'database':
            print(f'{name} is defined in the Moonraker configuration; skipping')
        elif cam['enabled'] != wanted[key]:
            request('POST', '/server/webcams/item', {'uid': cam['uid'], 'enabled': wanted[key]})


def main():
    parser = argparse.ArgumentParser(description='Show or hide the Forge-X camera and screen entries')
    for key in ENTRIES:
        parser.add_argument(f'--{key}', type=int, choices=(0, 1), required=True)
    parser.add_argument('--wait', type=int, default=0, help='seconds to wait for the API to start')
    args = parser.parse_args()
    try:
        set_enabled(api_request, {key: bool(getattr(args, key)) for key in ENTRIES}, args.wait)
    except (OSError, ValueError, KeyError) as error:
        print(f'Could not update the camera entries: {error}')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
