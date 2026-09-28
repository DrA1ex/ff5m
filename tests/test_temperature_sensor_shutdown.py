## Temperature sensor shutdown reason tests.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import importlib.util
import pathlib
import unittest
from unittest import mock


SENSOR_PATH = (pathlib.Path(__file__).parents[1] / ".py" / "klipper" /
               "patches" / "extras" / "temperature_sensor.py")
SPEC = importlib.util.spec_from_file_location(
    "patched_temperature_sensor", SENSOR_PATH)
SENSOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SENSOR)


class TemperatureSensorShutdownTest(unittest.TestCase):
    def test_weight_shutdown_records_triggering_load(self):
        sensor = SENSOR.PrinterSensorGeneric.__new__(
            SENSOR.PrinterSensorGeneric)
        sensor.name = "weightValue"
        sensor.printer = mock.Mock()
        sensor._template = lambda value: "ALARM\nM112"

        sensor._handle_exceed(1284.75)

        sensor.printer.invoke_shutdown.assert_called_once_with(
            "Shutdown due to sensor value exceeding the limit "
            "(weightValue: 1284.75 g)")

    def test_other_sensor_keeps_generic_shutdown_reason(self):
        sensor = SENSOR.PrinterSensorGeneric.__new__(
            SENSOR.PrinterSensorGeneric)
        sensor.name = "other"
        sensor.printer = mock.Mock()
        sensor._template = lambda value: "M112"

        sensor._handle_exceed(250.0)

        sensor.printer.invoke_shutdown.assert_called_once_with(
            "Shutdown due to sensor value exceeding the limit")


if __name__ == "__main__":
    unittest.main()
