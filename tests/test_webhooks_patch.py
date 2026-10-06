# Behavioral tests for stock-screen WebHooks transport across Klipper reloads.
#
# Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import collections
import gc
import importlib.util
import json
import pathlib
import select
import socket
import sys
import tempfile
import threading
import types
import unittest
import weakref
from unittest import mock


PATCHES = pathlib.Path(__file__).parents[1] / ".py/klipper/patches"


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, PATCHES / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GCODE = load_module("webhooks_test_gcode", "gcode.py")
CONFIGFILE = load_module("webhooks_test_configfile", "configfile.py")
with mock.patch.dict(sys.modules, {"gcode": GCODE}):
    WEBHOOKS = load_module("webhooks_test_module", "webhooks.py")


class SocketReactor:
    """Drive real Unix sockets and the callbacks scheduled by WebHooks."""

    def __init__(self):
        self.handles = {}
        self.callbacks = collections.deque()

    def mutex(self):
        return threading.Lock()

    def register_fd(self, fd, read, write=None):
        handle = types.SimpleNamespace(fd=fd, read=read, write=write,
                                       readable=True, writeable=False)
        self.handles[fd] = handle
        return handle

    def unregister_fd(self, handle):
        del self.handles[handle.fd]

    def set_fd_wake(self, handle, readable, writeable):
        self.assert_registered(handle)
        handle.readable, handle.writeable = readable, writeable

    def assert_registered(self, handle):
        if self.handles.get(handle.fd) is not handle:
            raise AssertionError("obsolete reactor registration")

    def register_callback(self, callback):
        self.callbacks.append(callback)

    def end(self):
        pass

    NOW = 0.
    NEVER = float("inf")

    def register_timer(self, callback, waketime):
        self.register_callback(callback)
        return callback

    def completion(self):
        result = []

        def wait():
            while not result:
                self.callbacks.popleft()(0.)
            return result[0]

        return types.SimpleNamespace(complete=result.append, wait=wait)

    def dispatch(self):
        while self.callbacks:
            self.callbacks.popleft()(0.)

    def poll(self):
        readable, writeable, _ = select.select(
            [h.fd for h in self.handles.values() if h.readable],
            [h.fd for h in self.handles.values() if h.writeable], [], 0.)
        for fd in readable:
            handle = self.handles.get(fd)
            if handle is not None:
                handle.read(0.)
        for fd in writeable:
            handle = self.handles.get(fd)
            if handle is not None:
                handle.write(0.)


class Printer:
    command_error = GCODE.CommandError
    config_error = ValueError

    def __init__(self, address, reason="startup", debuginput=None):
        self.start_args = dict(apiserver=address, start_reason=reason,
                               debuginput=debuginput)
        self.reactor = SocketReactor()
        self.run_result = None
        self.events = {}
        self.objects = {}
        self.objects["gcode"] = GCODE.GCodeDispatch(self)
        WEBHOOKS.add_early_printer_objects(self)

    def get_start_args(self):
        return self.start_args

    def get_reactor(self):
        return self.reactor

    def add_object(self, name, obj):
        self.objects[name] = obj

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)

    def lookup_objects(self):
        return list(self.objects.items())

    def register_event_handler(self, name, callback):
        self.events.setdefault(name, []).append(callback)

    def send_event(self, name, *args):
        for callback in self.events.get(name, []):
            callback(*args)

    def set_rollover_info(self, *args, **kwargs):
        pass

    def get_state_message(self):
        return "Config not loaded", "error"

    def request_exit(self, result):
        self.run_result = result
        self.reactor.end()

    def invoke_shutdown(self, message):
        raise AssertionError(message)

    @property
    def server(self):
        return self.objects["webhooks"].get_connection()

    @property
    def gcode(self):
        return self.objects["gcode"]

    def disconnect(self, result="exit"):
        self.run_result = result
        self.send_event("klippy:disconnect")


def encode(method, params=None, request_id=1):
    return json.dumps(dict(id=request_id, method=method,
                           params=params or {})).encode() + b"\x03"


class WebHooksReloadTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.address = str(pathlib.Path(self.directory.name) / "uds")
        self.printers = []
        self.peers = []
        self.addCleanup(self.close_all)
        self.printer = self.new_printer()

    def close_all(self):
        for printer in self.printers:
            if printer.server.sock is not None and printer.server.sock.fileno() >= 0:
                printer.disconnect()
        if hasattr(WEBHOOKS, "_close_stock_ui_connections"):
            WEBHOOKS._close_stock_ui_connections()
        for peer in self.peers:
            peer.close()

    def new_printer(self, reason="startup", address=None, **kwargs):
        printer = Printer(address or self.address, reason, **kwargs)
        self.printers.append(printer)
        return printer

    def connect(self, key=None):
        peer = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.peers.append(peer)
        peer.connect(self.address)
        peer.setblocking(False)
        self.printer.reactor.poll()
        if key is not None:
            self.request(peer, "gcode/subscribe_output",
                         dict(response_template=dict(key=key)))
        return peer

    def read_messages(self, peer):
        data = b""
        while True:
            try:
                part = peer.recv(65536)
            except BlockingIOError:
                break
            if not part:
                break
            data += part
        return [json.loads(part) for part in data.split(b"\x03") if part]

    def request(self, peer, method, params=None, request_id=1):
        peer.sendall(encode(method, params, request_id))
        self.printer.reactor.poll()
        self.printer.reactor.dispatch()
        return self.read_messages(peer)

    def reload(self, result="restart"):
        self.printer.disconnect(result)
        self.printer = self.new_printer(result)

    def test_ordinary_clients_disconnect_and_can_reconnect(self):
        peer = self.connect(123)
        self.reload()
        self.read_messages(peer)
        self.assertEqual(peer.recv(1), b"")
        replacement = self.connect()
        self.assertEqual(self.request(replacement, "info")[0]["result"]["state"], "error")

    def test_stock_transport_and_output_survive_repeated_reload(self):
        peer = self.connect(99999999)
        for result in ("restart", "restart", "firmware_restart", "restart"):
            self.reload(result)
            self.read_messages(peer)
            self.assertEqual(len(self.printer.server.clients), 1)
            self.assertEqual(len(self.printer.gcode.output_callbacks), 1)
            self.printer.gcode.respond_info("after reload")
            output = self.read_messages(peer)
            self.assertEqual(output, [dict(key=99999999, params=dict(response="// after reload"))])
            self.assertEqual(self.request(peer, "info")[0]["id"], 1)
            status = self.request(peer, "objects/query", dict(objects=dict(webhooks=None)))
            self.assertEqual(status[0]["result"]["status"]["webhooks"]["state"], "error")

    def test_terminal_and_unexpected_results_close_stock_connection(self):
        for result in ("exit", "error_exit", None, "unexpected"):
            with self.subTest(result=result):
                peer = self.connect(99999999)
                self.printer.disconnect(result)
                self.read_messages(peer)
                self.assertEqual(peer.recv(1), b"")
                self.printer = self.new_printer()

    def test_partial_request_survives_reload(self):
        peer = self.connect(99999999)
        message = encode("info", request_id=42)
        peer.sendall(message[:18])
        self.printer.reactor.poll()
        self.reload()
        self.read_messages(peer)
        peer.sendall(message[18:])
        self.printer.reactor.poll()
        self.printer.reactor.dispatch()
        self.assertEqual(self.read_messages(peer)[0]["id"], 42)

    def test_received_but_undispatched_request_survives_reload(self):
        peer = self.connect(99999999)
        peer.sendall(encode("info", request_id=43))
        self.printer.reactor.poll()
        self.reload()
        self.read_messages(peer)
        self.printer.reactor.dispatch()
        self.assertEqual(self.read_messages(peer)[0]["id"], 43)

    def test_query_waiting_for_status_is_answered_after_reload(self):
        class EndOldReactor(BaseException):
            pass

        peer = self.connect(99999999)
        old = self.printer

        def wait_for_status():
            old.disconnect("restart")
            raise EndOldReactor()

        complete = types.SimpleNamespace(complete=lambda value: None,
                                         wait=wait_for_status)
        peer.sendall(encode("objects/query", dict(objects=dict(webhooks=None)), 98888888))
        old.reactor.poll()
        with mock.patch.object(old.reactor, "completion", return_value=complete):
            with self.assertRaises(EndOldReactor):
                old.reactor.dispatch()
        self.read_messages(peer)
        self.printer = self.new_printer("restart")
        self.printer.reactor.dispatch()
        response = self.read_messages(peer)
        self.assertEqual(len(response), 1)
        self.assertEqual(response[0]["id"], 98888888)
        self.assertEqual(response[0]["result"]["status"]["webhooks"]["state"], "error")

    def test_interrupted_action_returns_error_without_repeating_effect(self):
        class EndOldReactor(BaseException):
            pass

        peer = self.connect(99999999)
        old = self.printer
        effects = []

        def action(script):
            effects.append(script)
            old.disconnect("restart")
            raise EndOldReactor()

        peer.sendall(encode("gcode/script", dict(script="ECHO ONCE"), 44))
        old.reactor.poll()
        with mock.patch.object(old.gcode, "run_script", side_effect=action):
            with self.assertRaises(EndOldReactor):
                old.reactor.dispatch()
        messages = self.read_messages(peer)
        self.printer = self.new_printer("restart")
        self.printer.reactor.dispatch()
        messages.extend(self.read_messages(peer))
        replies = [message for message in messages if message.get("id") == 44]
        self.assertEqual(len(replies), 1)
        self.assertIn("error", replies[0])
        self.assertEqual(effects, ["ECHO ONCE"])
        self.assertEqual(self.read_messages(peer), [])

    def test_api_and_script_restart_contracts(self):
        peer = self.connect(99999999)
        for method, params, result in (
                ("gcode/restart", {}, "restart"),
                ("gcode/firmware_restart", {}, "firmware_restart"),
                ("gcode/script", dict(script="RESTART"), "restart")):
            with self.subTest(method=method):
                self.assertEqual(self.request(peer, method, params), [dict(id=1, result={})])
                self.assertEqual(self.printer.run_result, result)
                self.reload(result)
                self.read_messages(peer)
                self.assertEqual(self.request(peer, "info")[0]["id"], 1)

    def test_save_config_writes_file_and_preserves_transport(self):
        peer = self.connect(99999999)
        cfgpath = pathlib.Path(self.directory.name) / "printer.cfg"
        cfgpath.write_text("[printer]\nkinematics: corexy\n")
        self.printer.start_args["config_file"] = str(cfgpath)
        config = CONFIGFILE.PrinterConfig(self.printer)
        # The printer uses Python 3.7; readfp was removed on the host Mac.
        parser = CONFIGFILE.configparser.RawConfigParser
        with mock.patch.object(parser, "readfp", parser.read_file, create=True):
            config.read_main_config()
            config.set("printer", "max_velocity", 123)
            config.cmd_SAVE_CONFIG(None)
        self.assertEqual(self.printer.run_result, "restart")
        self.assertIn("max_velocity = 123", cfgpath.read_text())
        self.reload(self.printer.run_result)
        self.read_messages(peer)
        self.assertEqual(self.request(peer, "info")[0]["id"], 1)

    def test_send_backpressure_and_buffer_order_survive_reload(self):
        peer = self.connect(99999999)
        client, = self.printer.server.clients.values()
        client.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
        response = dict(id=90, result="x" * 200000)
        client.send(response)
        self.assertTrue(client.is_blocking)
        self.reload()
        client, = self.printer.server.clients.values()
        data = b""
        for _ in range(100):
            while True:
                try:
                    chunk = peer.recv(65536)
                except BlockingIOError:
                    break
                self.assertTrue(chunk)
                data += chunk
            self.printer.reactor.poll()
            if not client.send_buffer:
                while True:
                    try:
                        data += peer.recv(65536)
                    except BlockingIOError:
                        break
                break
        self.assertFalse(client.send_buffer)
        self.assertFalse(client.is_blocking)
        messages = [json.loads(part) for part in data.split(b"\x03") if part]
        self.assertEqual(messages[0], response)
        self.assertEqual(messages[1]["key"], 99999999)
        self.assertEqual(self.request(peer, "info")[0]["id"], 1)

    def test_restore_failure_closes_socket_and_keeps_api_available(self):
        for stage in ("register", "subscribe"):
            with self.subTest(stage=stage):
                peer = self.connect(99999999)
                self.printer.disconnect("restart")
                original = SocketReactor.register_fd

                def register(reactor, fd, read, write=None):
                    if write is not None:
                        raise RuntimeError("fd registration failed")
                    return original(reactor, fd, read, write)

                patch = (mock.patch.object(SocketReactor, "register_fd", register)
                         if stage == "register" else
                         mock.patch.object(WEBHOOKS.GCodeHelper, "subscribe_output",
                                           side_effect=RuntimeError("subscription failed")))
                with patch, self.assertLogs(level="ERROR"):
                    self.printer = self.new_printer("restart")
                self.read_messages(peer)
                self.assertEqual(peer.recv(1), b"")
                self.assertFalse(self.printer.server.clients)
                self.assertEqual(len(self.printer.reactor.handles), 1)
                replacement = self.connect()
                self.assertEqual(self.request(replacement, "info")[0]["id"], 1)
                self.reload()

    def test_incompatible_start_closes_preserved_socket(self):
        for options in (dict(reason="startup"), dict(reason="restart", debuginput="file"),
                        dict(reason="restart", address=self.address + "-other")):
            with self.subTest(options=options):
                peer = self.connect(99999999)
                self.printer.disconnect("restart")
                other = self.new_printer(**options)
                self.read_messages(peer)
                self.assertEqual(peer.recv(1), b"")
                other.disconnect()
                self.printer = self.new_printer()

    def test_listener_start_failure_closes_preserved_socket(self):
        peer = self.connect(99999999)
        self.printer.disconnect("restart")
        with mock.patch.object(WEBHOOKS.ServerSocket, "_remove_socket_file",
                               side_effect=OSError("socket path unavailable")):
            with self.assertRaisesRegex(OSError, "socket path unavailable"):
                self.new_printer("restart")
        self.read_messages(peer)
        self.assertEqual(peer.recv(1), b"")

    def test_disconnected_peer_is_removed_after_reload(self):
        peer = self.connect(99999999)
        peer.close()
        self.reload()
        self.printer.reactor.poll()
        self.assertFalse(self.printer.server.clients)
        self.assertEqual(len(self.printer.reactor.handles), 1)

    def test_ordinary_subscription_is_not_restored_with_stock_subscription(self):
        stock = self.connect(99999999)
        other = self.connect(123)
        self.reload()
        self.read_messages(stock)
        self.read_messages(other)
        self.assertEqual(other.recv(1), b"")
        self.printer.gcode.respond_info("stock only")
        self.assertEqual(self.read_messages(stock), [dict(key=99999999, params=dict(response="// stock only"))])

    def test_resubscription_replaces_template_and_registers_one_handler(self):
        peer = self.connect(99999999)
        template = dict(key=99999999, extra="retained")
        self.request(peer, "gcode/subscribe_output", dict(response_template=template))
        self.reload()
        self.read_messages(peer)
        self.printer.gcode.respond_info("one response")
        self.assertEqual(self.read_messages(peer), [dict(template, params=dict(response="// one response"))])
        self.assertEqual(len(self.printer.gcode.output_callbacks), 1)
        self.request(peer, "gcode/subscribe_output", dict(response_template=dict(key=5)))
        self.reload()
        self.read_messages(peer)
        self.assertEqual(peer.recv(1), b"")

    def test_old_reactor_and_printer_can_be_collected(self):
        peer = self.connect(99999999)
        old = weakref.ref(self.printer)
        old_reactor = weakref.ref(self.printer.reactor)
        self.reload()
        self.printers.pop(0)
        gc.collect()
        self.assertIsNone(old())
        self.assertIsNone(old_reactor())
        self.assertEqual(self.request(peer, "info")[-1]["id"], 1)


if __name__ == "__main__":
    unittest.main()
