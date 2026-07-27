## [gcode_macro LOAD_CELL_TARE]
##
## Copyright (C) 2025, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license


import logging
import time


class LoadCellTareGcode:
    def __init__(self, config):
        self.config = config
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object("gcode")

        self.printer.register_event_handler("klippy:ready", self._init)

        self.gcode.register_command("LOAD_CELL_TARE", self.cmd_LOAD_CELL_TARE)

    def _init(self):
        self.toolhead = self.printer.lookup_object("toolhead")
        self.weight = self.printer.lookup_object("temperature_sensor weightValue")
        self.probe = self.printer.lookup_object("probe")
        self.mod_params = self.printer.lookup_object("mod_params")
        self.level_pin = self.printer.lookup_object("gcode_button check_level_pin")

    def _run_gcode(self, *cmds: str):
        self.gcode.run_script_from_command("\n".join(cmds))

    def _tare_confirmed(self):
        return bool(self.level_pin.last_state)

    def cmd_LOAD_CELL_TARE(self, gcmd):
        t = time.time()

        weight = self.weight.last_temp
        threshold_weight = self.mod_params.variables.get("cell_weight", 0)

        if weight < threshold_weight:
            logging.info(f"LOAD_CELL_TARE: Skipped (weight {weight} within threshold {threshold_weight})")
            return

        logging.info(f"LOAD_CELL_TARE: Started load cell tare. Weight: {weight}, threshold: {threshold_weight}")

        # Check tare confirmation state reset
        for i in range(5):
            if not self._tare_confirmed():
                break

            gcmd.respond_info(f"Attempt {i + 1}. Tare conformation is not clear. Try to reset...")
            self._reset_tare_confirmation()
            self._run_gcode("WAIT TIME=100")
        else:
            return self._raise_error("Tare conformation did not reset.")

        # Check bed pressure to ensure no toolhead contact
        # Taring in that case would be incorrect
        self._query_probe()

        # Try to tare several times, as events may not be received in a single attempt
        ok = False
        for i in range(5):
            self._cell_tare()
            if self._tare_confirmed():
                ok = True
                break

            logging.info(f"LOAD_CELL_TARE: Attempt {i + 1}. No confirmation from level sensor. Weight: {self.weight.last_temp}")

        self._reset_tare_confirmation()

        if ok:
            logging.info("LOAD_CELL_TARE: Tare confirmed.")

            self._run_gcode("WAIT TIME=100")
            if abs(self.weight.last_temp) > threshold_weight:
                return self._raise_error(f"Load cell tare failed: weight {self.weight.last_temp} > threshold {threshold_weight}")

        elif self.config.getint("skip_tare_error", 0):
            return self._raise_error("Load cell tared, but no confirmation from level_pin; configured to skip.")

        else:
            return self._raise_error("Load cell tare failed. No tare confirmation received")

        # If we are here - tare is considered successful
        logging.info(f"LOAD_CELL_TARE: Load cell tare finished in {time.time() - t:0.1f}s.")

    def _query_probe(self):
        # This may trigger a "Timer too close" error.
        # self._run_gcode("QUERY_PROBE")

        # Instead, we simply check the weight value since the MCU handles this in QUERY_PROBE.
        # It checks if the weight is greater than 200; if so, the probe is considered triggered

        weight = self.weight.last_temp
        if weight < 200:
            logging.info("LOAD_CELL_TARE: No pressure to bed detected. OK!")
            return

        logging.info("LOAD_CELL_TARE: Detected bed pressure.")
        self.gcode.respond_raw("!! Detected bed pressure. Please ensure the bed is clean!")

        self.gcode.run_script_from_command("SAVE_GCODE_STATE NAME=CELL_TARE")

        kin_status = self.toolhead.get_kinematics().get_status(0)
        safe_z = abs(float(self.mod_params.variables.get("safe_z", 10.0)))
        if "z" not in kin_status['homed_axes']:
            logging.info("LOAD_CELL_TARE: Start Z homing...")
            self._run_gcode(
                "G28 Z",
                "M400"
            )
        elif self.toolhead.get_position()[2] < safe_z:  # position.z
            logging.info("LOAD_CELL_TARE: Moving bed lower...")
            self._run_gcode(
                "G90",
                "G1 Z%g F6000" % safe_z,
                "M400",
            )

        self.gcode.run_script_from_command("RESTORE_GCODE_STATE NAME=CELL_TARE")

    def _raise_error(self, msg):
        start_print_vars = self.printer.lookup_object('gcode_macro _START_PRINT').variables
        if start_print_vars["print_active"]:
            if self.mod_params.variables['display'] != 0:
                self.gcode.run_script_from_command('CANCEL_PRINT REASON="Cell tare failed!"')
            else:
                self.gcode.run_script_from_command('CANCEL_PRINT')

        raise self.gcode.error(msg)

    def _cell_tare(self):
        logging.info("LOAD_CELL_TARE: Send tare request.")

        # Tare is set by toggling level_h1 pin
        timeout = 250
        self._run_gcode(
            "SET_PIN PIN=level_h1 VALUE=0",
            f"WAIT TIME={timeout}",
            "SET_PIN PIN=level_h1 VALUE=1",
            f"WAIT TIME={timeout}",
            "SET_PIN PIN=level_h1 VALUE=0",
            f"WAIT TIME={timeout}",
            "SET_PIN PIN=level_h1 VALUE=1",
            f"WAIT TIME={timeout}",
        )

    def _reset_tare_confirmation(self):
        logging.info("LOAD_CELL_TARE: Reset tare confirmation.")

        # Toggle level clear pins
        # This action resets level_pin, which we read later to confirm that the tare was reset.
        timeout = 10
        self._run_gcode(
            "SET_PIN PIN=level_clear VALUE=0",
            f"WAIT TIME={timeout}",
            "SET_PIN PIN=level_clear VALUE=1",
            f"WAIT TIME={timeout}",
        )


def load_config(config):
    return LoadCellTareGcode(config)
