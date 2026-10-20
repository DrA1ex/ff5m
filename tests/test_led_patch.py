## Behavioral tests for PWM LED polarity and startup.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import importlib.util
import pathlib
import sys
import types
import unittest
from unittest import mock


LED_PATH = pathlib.Path(__file__).parents[1] / ".py/klipper/patches/extras/led.py"
SPEC = importlib.util.spec_from_file_location("led_test_extras.led", LED_PATH)
LED = importlib.util.module_from_spec(SPEC)
DISPLAY = types.ModuleType("led_test_extras.display")
DISPLAY.display = types.SimpleNamespace()
with mock.patch.dict(sys.modules, {"led_test_extras.display": DISPLAY}):
    SPEC.loader.exec_module(LED)


class PWMLEDTest(unittest.TestCase):
    def make_led(self, invert, initial=0., channel="white"):
        values = {"invert": invert, channel + "_pin": "PA8",
                  "initial_" + channel.upper(): initial}
        self.pin = mock.Mock()
        self.gcode = mock.Mock()
        self.printer = mock.Mock(command_error=RuntimeError)
        self.printer.lookup_object.side_effect = {
            "pins": types.SimpleNamespace(setup_pin=lambda *args: self.pin),
            "gcode": self.gcode,
        }.__getitem__
        self.printer.load_object.return_value = types.SimpleNamespace(
            setup_helper=LED.LEDHelper)
        self.printer.get_reactor.return_value.monotonic.return_value = 10.
        self.pin.get_mcu.return_value.estimated_print_time.return_value = 10.
        config = types.SimpleNamespace(
            get_printer=lambda: self.printer,
            get_name=lambda: "led chamber_light",
            get=lambda key, default=None: values.get(key, default),
            getfloat=lambda key, default, **kwargs: values.get(key, default),
            getboolean=lambda key, default: values.get(key, default),
        )
        driver = LED.load_config_prefix(config)
        self.set_led = self.gcode.register_mux_command.call_args.args[3]
        return driver

    def command(self, **values):
        self.set_led(types.SimpleNamespace(
            get_float=lambda key, default, **kwargs: values.get(key, default),
            get_int=lambda key, default, **kwargs: values.get(key, default)))

    def test_startup_and_shutdown_use_the_configured_polarity(self):
        for channel in ("red", "green", "blue", "white"):
            for invert in (False, True):
                for initial in (0., 0.25, 1.):
                    with self.subTest(channel=channel, invert=invert, initial=initial):
                        driver = self.make_led(invert, initial, channel)
                        self.pin.setup_start_value.assert_called_once_with(
                            1. - initial if invert else initial, 1. if invert else 0.)
                        index = ("red", "green", "blue", "white").index(channel)
                        self.assertEqual(driver.get_status()["color_data"][0][index], initial)

    def test_redundant_startup_off_command_keeps_inverted_led_off(self):
        driver = self.make_led(True)
        self.command(WHITE=0., SYNC=0)
        self.pin.setup_start_value.assert_called_once_with(1., 1.)
        self.pin.set_pwm.assert_not_called()
        self.assertEqual(driver.get_status()["color_data"][0][3], 0.)

    def test_manual_brightness_changes_keep_working_with_both_polarities(self):
        for invert in (False, True):
            driver = self.make_led(invert)
            for white in (1., 0.4, 0.):
                with self.subTest(invert=invert, white=white):
                    self.command(WHITE=white, SYNC=0)
                    self.assertEqual(self.pin.set_pwm.call_args.args[1], 1. - white if invert else white)
                    self.assertEqual(driver.get_status()["color_data"][0][3], white)


if __name__ == "__main__":
    unittest.main()
