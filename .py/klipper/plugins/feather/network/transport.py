# Unix socket transport for the netd line protocol.
#
# Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
#
# This file may be distributed under the terms of the GNU GPLv3 license

"""Non-blocking Unix socket lifecycle and newline framing for netd."""

import errno
import logging
import socket


SOCKET_PATH = "/run/netd.sock"
_READ_SIZE = 8192
_MAX_PARTIAL = 262144


class NetdTransport:
    """One non-blocking line-oriented Unix socket."""

    def __init__(self, path=SOCKET_PATH, opener=None):
        self.path = path
        self._opener = opener or self._open_socket
        self._socket = None
        self._partial = ""

    def _open_socket(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(0.0)
        sock.connect(self.path)
        return sock

    @property
    def connected(self):
        return self._socket is not None

    def fileno(self):
        return None if self._socket is None else self._socket.fileno()

    def connect(self):
        if self._socket is not None:
            return True
        try:
            self._socket = self._opener()
        except (OSError, socket.error) as exc:
            if exc.errno not in (errno.ENOENT, errno.ECONNREFUSED,
                                 errno.EACCES, errno.EAGAIN):
                logging.warning("[feather_network] unable to reach netd: %s", exc)
            self._socket = None
            return False
        self._partial = ""
        return True

    def close(self):
        if self._socket is None:
            return
        try:
            self._socket.close()
        except OSError:
            pass
        self._socket = None
        self._partial = ""

    def send(self, command):
        if self._socket is None:
            return False
        try:
            self._socket.sendall((command + "\n").encode("utf-8"))
            return True
        except (OSError, socket.error) as exc:
            logging.warning("[feather_network] netd write failed: %s", exc)
            self.close()
            return False

    def read_lines(self):
        """Return complete lines currently readable from the stream."""
        if self._socket is None:
            return []
        try:
            data = self._socket.recv(_READ_SIZE)
        except (BlockingIOError, InterruptedError):
            return []
        except (OSError, socket.error) as exc:
            if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EINTR):
                return []
            logging.warning("[feather_network] netd read failed: %s", exc)
            self.close()
            return []

        if not data:
            self.close()
            return []

        text = self._partial + data.decode("utf-8", "replace")
        lines = text.split("\n")
        self._partial = lines.pop()
        if len(self._partial) > _MAX_PARTIAL:
            logging.warning("[feather_network] oversized netd line discarded")
            self._partial = ""
        return lines
