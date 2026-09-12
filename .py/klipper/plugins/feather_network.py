# Application-facing client for netd.
#
# Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
#
# This file may be distributed under the terms of the GNU GPLv3 license

"""Keep Feather attached to netd and expose its current daemon snapshot.

The daemon owns network retry and recovery policy. This client only reconnects
its IPC socket after netd restarts; losing the socket never means cancelling a
daemon-owned network operation.
"""

import logging

import feather_netd_protocol as protocol
from feather_netd_transport import NetdTransport, SOCKET_PATH


RETRY_INTERVAL = 5.0


class NetworkClient:
    """Application-facing netd client with one authoritative local snapshot.

    ``on_event(kind, value, eventtime)`` is the single asynchronous boundary to
    the UI. Kinds are ``snapshot``, ``scan``, ``ok``, ``error`` and
    ``unavailable``.
    """

    def __init__(self, reactor, on_event, path=SOCKET_PATH, opener=None,
                 transport=None):
        self._reactor = reactor
        self._on_event = on_event
        self._transport = transport or NetdTransport(path, opener)
        self._handle = None
        self._next_retry = 0.0
        self.status = protocol.blank_status()

    @property
    def connected(self):
        return self._transport.connected

    def start(self):
        self.service(self._reactor.monotonic())

    def service(self, eventtime):
        """Reconnect the IPC socket when needed and drain available input."""
        if not self.connected:
            if eventtime < self._next_retry:
                return
            self._unregister_fd()
            self._next_retry = eventtime + RETRY_INTERVAL
            if not self._attach():
                self._set_unavailable(eventtime)
                return
        self._drain(eventtime)

    def stop(self):
        """Stop IPC observation without cancelling any daemon operation."""
        self._unregister_fd()
        self._transport.close()

    def mark_unresponsive(self, eventtime):
        """Discard an unresponsive socket and retry through a fresh one."""
        self._unregister_fd()
        self._transport.close()
        self._next_retry = eventtime + RETRY_INTERVAL
        self._set_unavailable(eventtime)

    def get_state(self):
        return self._send("GET")

    def scan(self):
        return self._send("SCAN")

    def use_ethernet(self):
        return self._send("USE_ETHERNET")

    def connect_wifi(self, ssid, password=None):
        return self._send(protocol.connect_wifi_command(ssid, password))

    def cancel(self):
        return self._send("CANCEL")

    def _send(self, command):
        if self._transport.send(command):
            return True
        # A failed write closes the transport. Drop the old reactor handle now
        # so a later IPC reconnect always registers the new descriptor.
        self._unregister_fd()
        return False

    def _attach(self):
        if not self._transport.connect():
            return False
        if not self._transport.send("SUBSCRIBE"):
            self._transport.close()
            return False
        descriptor = self._transport.fileno()
        if descriptor is not None:
            self._handle = self._reactor.register_fd(
                descriptor, self._on_readable)
        return True

    def _unregister_fd(self):
        if self._handle is None:
            return
        try:
            self._reactor.unregister_fd(self._handle)
        except Exception:
            logging.exception(
                "[feather_network] unable to unregister the netd socket")
        self._handle = None

    def _on_readable(self, eventtime):
        self._drain(eventtime)

    def _drain(self, eventtime):
        lines = self._transport.read_lines()
        if not self.connected:
            self._unregister_fd()
            self._set_unavailable(eventtime)
            return
        for line in lines:
            parsed = protocol.parse_message(line)
            if parsed is None:
                continue
            kind, value = parsed
            if kind == "status":
                key, field_value = value
                changed = self.status.get(key) != field_value
                if changed:
                    self.status[key] = field_value
                # Report receipt even when the field is unchanged: daemon input
                # is also the UI request watchdog's liveness signal.
                self._on_event("snapshot", changed, eventtime)
                continue
            self._on_event(kind, value, eventtime)

    def _set_unavailable(self, eventtime):
        changed = self.status != protocol.OFFLINE_STATUS
        protocol.reset_status(self.status)
        self._on_event("unavailable", changed, eventtime)
