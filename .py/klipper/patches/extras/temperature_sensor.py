# Support generic temperature sensors
#
# Changes:
# - Added gcode code to execute if value out of range
# - Added optional min_exceed_count debounce: require N consecutive
#   over-threshold samples before running exceed_gcode. Without this a
#   single noisy/transient reading (e.g. a print blob briefly loading the
#   AD5M's bed load cell) is enough to trip the immediate M112 shutdown
#   path below, with no way to tell that apart from a genuine, sustained
#   nozzle collision. Defaults to 1 (fires on the first sample, identical
#   to prior behavior) so existing configs are unaffected.
#
# Copyright (C) 2025, Alexander K <https://github.com/drA1ex>
#
# Copyright (C) 2019  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.


import logging, re

KELVIN_TO_CELSIUS = -273.15


class PrinterSensorGeneric:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.name = config.get_name().split()[-1]
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object("gcode")
        pheaters = self.printer.load_object(config, 'heaters')
        self.sensor = pheaters.setup_sensor(config)
        self.min_temp = config.getfloat('min_temp', KELVIN_TO_CELSIUS,
                                        minval=KELVIN_TO_CELSIUS)
        self.max_temp = config.getfloat('max_temp', 99999999.9,
                                        above=self.min_temp)

        self.trigger_value = config.getfloat('trigger_value', None, above=self.min_temp, below=self.max_temp)
        self.gcode_throttle = config.getfloat("throttle", 1., minval=0)
        self.gcode_reschedule = config.getboolean("reschedule", False)
        self.gcode_reschedule_cooldown = config.getfloat("reschedule_cooldown", 1., minval=0)
        self.min_exceed_count = config.getint("min_exceed_count", 1, minval=1)
        gcode_macro = self.printer.load_object(config, "gcode_macro")
        self.exceed_gcode_present = config.get("exceed_gcode", None) is not None
        if self.exceed_gcode_present:
            self.exceed_template = gcode_macro.load_template(config, "exceed_gcode")

        self.sensor.setup_minmax(self.min_temp, self.max_temp)
        self.sensor.setup_callback(self.temperature_callback)
        pheaters.register_sensor(config, self)
        self.last_temp = 0.
        self.measured_min = 99999999.
        self.measured_max = 0.
        self._throttle_max_value = float("-inf")
        self._callback_scheduled = False
        self._last_exceed = 0
        self._consecutive_exceed = 0

    m112_r = re.compile(r"^(?:[nN][0-9]+)?\s*[mM]112(?:\s|$)", re.MULTILINE)

    def temperature_callback(self, read_time, temp):
        self.last_temp = temp
        if temp:
            self.measured_min = min(self.measured_min, temp)
            self.measured_max = max(self.measured_max, temp)

            if self.exceed_gcode_present:
                if temp >= self.trigger_value:
                    self._consecutive_exceed += 1
                    if self._consecutive_exceed >= self.min_exceed_count:
                        self._handle_exceed(temp)
                else:
                    self._consecutive_exceed = 0

    def _template(self, value):
        context = self.exceed_template.create_template_context()
        context["value"] = value

        return self.exceed_template.render(context)

    def _handle_exceed(self, temp):
        logging.info(f"[temperature_sensor {self.name}]: Out of range ({temp})")

        template = self._template(temp)
        # Run M112 immediately if present
        if self.m112_r.search(template):
            self.printer.invoke_shutdown("Shutdown due to sensor value exceeding the limit")
            return

        now = self.reactor.monotonic()
        next_event = self._last_exceed + self.gcode_throttle
        delta = next_event - now

        if delta <= 0:
            self._throttle_max_value = float("-inf")

        if (
                self._callback_scheduled
                or (self.gcode_reschedule and delta > 0 and self.gcode_throttle - delta < self.gcode_reschedule_cooldown)
                or (not self.gcode_reschedule and now < next_event and temp <= self._throttle_max_value)
        ):
            logging.info(f"[temperature_sensor {self.name}]: Exceed event skipped")
            return

        when = self.reactor.NOW
        rescheduled = False

        if self.gcode_reschedule and delta > 0:
            when = now + delta
            rescheduled = True
            logging.info(f"[temperature_sensor {self.name}]: Reschedule exceed event after {delta:.2f}")

        self._callback_scheduled = True
        self._throttle_max_value = max(temp, self._throttle_max_value)
        self.reactor.register_callback(lambda _, rs=rescheduled, tp=template: self._exceed_cb(tp, rs), when)

    def _exceed_cb(self, template, rescheduled):
        # Re-render, as variables may change
        if rescheduled:
            template = self._template(self._throttle_max_value)

        try:
            logging.info(f"[temperature_sensor {self.name}]: Run exceed event gcode")
            self.gcode.run_script(template)
        except:
            logging.exception(f"[temperature_sensor {self.name}]: Script running error:\n{template}")

        self._callback_scheduled = False
        self._last_exceed = self.reactor.monotonic()

    def get_temp(self, eventtime):
        return self.last_temp, 0.

    def stats(self, eventtime):
        return False, '%s: temp=%.1f' % (self.name, self.last_temp)

    def get_status(self, eventtime):
        return {
            'temperature': round(self.last_temp, 2),
            'measured_min_temp': round(self.measured_min, 2),
            'measured_max_temp': round(self.measured_max, 2)
        }


def load_config_prefix(config):
    return PrinterSensorGeneric(config)
