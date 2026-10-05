## Direct polling for the temperature-wait macro.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license


class TemperatureWait:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object("gcode")
        self.reactor = self.printer.get_reactor()
        self.gcode.register_command(
            "_WAIT_TEMPERATURE_POLL", self.cmd_WAIT_TEMPERATURE_POLL)

    def _cancel_point(self, context, gcmd):
        if context.contexts:
            if context.pending_cancel is not None:
                self.gcode.run_script_from_command(
                    "_WAIT_TEMPERATURE_RESET_STATE")
            context.cancellation_point(gcmd)

    def cmd_WAIT_TEMPERATURE_POLL(self, gcmd):
        wait = self.printer.lookup_object("gcode_macro _WAIT_TEMPERATURE")
        try:
            cmd = gcmd.get("CMD")
            if cmd not in ("M104", "M140"):
                raise gcmd.error("CMD must be M104 or M140")
            value = gcmd.get_int("VALUE")
            checks = gcmd.get_int("CHECKS")
            delay = gcmd.get_int("DELAY")
            minimum = gcmd.get_float("MINIMUM", float("-inf"))
            maximum = gcmd.get_float("MAXIMUM", float("inf"))
            if minimum > maximum:
                raise gcmd.error("MINIMUM must not be greater than MAXIMUM")

            context = self.printer.lookup_object("operation_context")
            if cmd == "M104":
                heater = self.printer.lookup_object("extruder").get_heater()
            else:
                heater = self.printer.lookup_object("heaters").lookup_heater("heater_bed")
            target_command = "%s S%d" % (cmd, value)
            wait_command = "WAIT TIME=%d" % delay

            for _ in range(checks):
                self._cancel_point(context, gcmd)
                # M108 replaces variables, so re-read the macro's live flags.
                if (wait.variables["cancel"]
                        or wait.variables["temperature_reached"]):
                    return
                # Match the old check's observation before executing M104/M140.
                temperature = round(heater.get_temp(self.reactor.monotonic())[0], 2)
                self.gcode.run_script_from_command(target_command)
                self._cancel_point(context, gcmd)
                if wait.variables["cancel"]:
                    return
                if minimum <= temperature <= maximum:
                    self.gcode.run_script_from_command(
                        "SET_GCODE_VARIABLE MACRO=_WAIT_TEMPERATURE "
                        "VARIABLE=temperature_reached VALUE=True")
                    return
                # WAIT includes G4 and M400; retain its motion synchronization.
                self.gcode.run_script_from_command(wait_command)

            self._cancel_point(context, gcmd)
        except Exception:
            # The final-check macro is skipped when polling raises an error.
            self.gcode.run_script_from_command("_WAIT_TEMPERATURE_RESET_STATE")
            raise


def load_config(config):
    return TemperatureWait(config)
