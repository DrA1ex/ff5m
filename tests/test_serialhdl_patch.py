## Behavioral tests for stock AD5M UART transport negotiation.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license.

import importlib.util
import pathlib
import sys
import types
import unittest
from unittest import mock


MODULE_PATH = (pathlib.Path(__file__).parents[1] / ".py" / "klipper" /
               "patches" / "serialhdl.py")
SPEC = importlib.util.spec_from_file_location("forge_x_serialhdl", MODULE_PATH)
SERIALHDL = importlib.util.module_from_spec(SPEC)
with mock.patch.dict(sys.modules, {
        name: types.ModuleType(name)
        for name in ("serial", "msgproto", "chelper", "util")}):
    SPEC.loader.exec_module(SERIALHDL)


class SerialSessionTest(unittest.TestCase):
    def setUp(self):
        self.reader = SERIALHDL.SerialReader.__new__(SERIALHDL.SerialReader)
        self.reader.warn_prefix = "eboard: "
        self.reader.ffi_main = mock.Mock()
        self.reader.ffi_main.gc.side_effect = lambda value, cleanup: value
        self.library = self.reader.ffi_lib = mock.Mock()
        self.reader.reactor = mock.Mock()
        self.reader.reactor.monotonic.return_value = 10.0
        self.completion = self.reader.reactor.register_callback.return_value
        self.completion.wait.return_value = b"mcu identify dictionary"
        self.reader.register_response = mock.Mock()
        self.reader.disconnect = mock.Mock()
        self.device = types.SimpleNamespace(fileno=lambda: 42, baudrate=250000)
        self.parser = mock.Mock()
        self.parser_factory = mock.patch.object(
            SERIALHDL.msgproto, "MessageParser", return_value=self.parser,
            create=True)
        self.parser_factory.start()
        self.addCleanup(self.parser_factory.stop)
        self.thread_factory = mock.patch.object(SERIALHDL.threading, "Thread")
        self.thread = self.thread_factory.start()
        self.addCleanup(self.thread_factory.stop)
        self.constants = {}
        self.parser.get_constant_float.side_effect = self.constant
        self.parser.get_constant_int.side_effect = self.constant

    def constant(self, name, default):
        return self.constants.get(name, default)

    def start(self, transport=b'u'):
        self.assertTrue(self.reader._start_session(self.device, transport))
        self.parser.process_identify.assert_called_once_with(
            b"mcu identify dictionary")

    def assert_transport_values(self, frequency, window):
        queue = self.reader.serialqueue
        if frequency is None:
            self.library.serialqueue_set_wire_frequency.assert_not_called()
        else:
            self.library.serialqueue_set_wire_frequency.assert_called_once_with(
                queue, frequency)
        if window is None:
            self.library.serialqueue_set_receive_window.assert_not_called()
        else:
            self.library.serialqueue_set_receive_window.assert_called_once_with(
                queue, window)

    def test_stock_uart_uses_configured_baud_and_verified_receive_window(self):
        with self.assertLogs(level="INFO") as logs:
            self.start()
        self.assert_transport_values(250000, 192)
        self.assertTrue(any("SERIAL_BAUD" in line for line in logs.output))
        self.assertTrue(any("RECEIVE_WINDOW" in line for line in logs.output))

    def test_firmware_values_take_precedence_over_uart_fallbacks(self):
        self.constants.update(SERIAL_BAUD=115200, RECEIVE_WINDOW=256)
        self.start()
        self.assert_transport_values(115200, 256)

    def test_each_missing_uart_value_is_filled_independently(self):
        for constants, frequency, window in (
                ({"SERIAL_BAUD": 115200}, 115200, 192),
                ({"RECEIVE_WINDOW": 128}, 250000, 128)):
            with self.subTest(constants=constants):
                self.constants.clear()
                self.constants.update(constants)
                self.parser.reset_mock()
                self.library.reset_mock()
                self.start()
                self.assert_transport_values(frequency, window)

    def test_uart_device_without_baud_rate_leaves_wire_frequency_unset(self):
        del self.device.baudrate
        self.start()
        self.assert_transport_values(None, 192)

    def test_fallbacks_do_not_apply_to_can_connections(self):
        self.start(b'c')
        self.assert_transport_values(None, None)

    def test_can_uses_can_frequency_and_firmware_receive_window(self):
        self.constants.update(CANBUS_FREQUENCY=1000000, SERIAL_BAUD=115200,
                              RECEIVE_WINDOW=128)
        self.start(b'c')
        self.assert_transport_values(1000000, 128)

    def test_identify_timeout_disconnects_without_applying_fallbacks(self):
        self.completion.wait.return_value = None
        self.assertFalse(self.reader._start_session(self.device))
        self.reader.disconnect.assert_called_once_with()
        self.assert_transport_values(None, None)
        self.parser.process_identify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
