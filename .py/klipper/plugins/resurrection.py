## Resurrection plugin implementation
##
## Copyright (C) 2025, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import enum, json, logging, os


class ResurrectorState(enum.Enum):
    UNKNOWN = 0

    IDLE = 10
    RESURRECTION = 20

    PRINTING = 30
    PAUSED = 40

    ERROR = 50

    DESTROYED = 100


class Resurrector:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()

        self.gcode = self.printer.lookup_object("gcode")

        self.state = ResurrectorState.UNKNOWN

        self.dump_time = config.getfloat("dump_time", 3.)
        self.file_path = config.get("filename")

        self.printer.register_event_handler("klippy:ready", self._init)

        self.gcode.register_command("RESURRECT", self.cmd_RESURRECT)

    def _init(self):
        mod_params = self.printer.lookup_object("mod_params")

        if not mod_params.variables['power_loss_recovery']:
            logging.info("[resurrection] Disabled due to 'power_loss_recovery' parameter.")
            return

        self.printer.register_event_handler("klippy:shutdown", self._shutdown)
        self.printer.register_event_handler("klippy:disconnect", self._shutdown)

        self.toolhead = self.printer.lookup_object("toolhead")
        self.virtual_sdcard = self.printer.lookup_object("virtual_sdcard")
        self.print_stats = self.printer.lookup_object("print_stats")

        self.extruder = self.printer.lookup_object("extruder")
        self.heater_bed = self.printer.lookup_object("heater_bed")
        self.bed_mesh = self.printer.lookup_object("bed_mesh")

        self.start_print_macro = self.printer.lookup_object('gcode_macro _START_PRINT')

        if os.path.isfile(self.file_path):
            logging.info("[resurrection] Resurrection file exists.")
            self._change_state(ResurrectorState.RESURRECTION)

            def _initial_msg(_):
                self.gcode.respond_raw("// action:prompt_begin Resurrection")
                self.gcode.respond_raw("// action:prompt_text Resurrection is available! Would you like to restore the print?")
                self.gcode.respond_raw("// action:prompt_footer_button Restore|RESURRECT")
                self.gcode.respond_raw("// action:prompt_footer_button Abort|RESPOND TYPE=command MSG=action:prompt_end")
                self.gcode.respond_raw("// action:prompt_show")

                self.gcode.respond_raw("// Resurrection is available!")
                self.gcode.respond_raw("// Run RESURRECT to restore the print")

            self.reactor.register_callback(_initial_msg, waketime=self.reactor.monotonic() + 3)
        else:
            logging.info("[resurrection] Resurrection file doesn't exists")
            self._change_state(ResurrectorState.IDLE)

        self._timer = self.reactor.register_timer(self._dump_timer_handler, self.reactor.NOW)

    def _shutdown(self):
        if self._timer:
            self.reactor.unregister_timer(self._timer)
            self._timer = None

            if self.state == ResurrectorState.PRINTING:
                self._dump(self.reactor.NOW)

            self._change_state(ResurrectorState.DESTROYED)

    def _dump_timer_handler(self, eventtime):
        if self.state == ResurrectorState.DESTROYED:
            return

        stats = self.print_stats.get_status(eventtime)
        stats_state = stats["state"]

        if stats_state == "printing" and self.start_print_macro.variables["print_started"]:
            self._change_state(ResurrectorState.PRINTING)
        elif stats_state in {"complete", "cancelled"} and self.state != ResurrectorState.IDLE:
            self._change_state(ResurrectorState.IDLE)
            self._clear(eventtime)

        if self.state == ResurrectorState.PRINTING:
            if stats_state == "printing":
                self._dump(eventtime)
            elif stats_state == "paused":
                self._change_state(ResurrectorState.PAUSED)
                self._dump(eventtime)
            elif stats_state == "error":
                self._change_state(ResurrectorState.IDLE)
                self._dump(eventtime)
            elif stats_state == "idle":
                self._change_state(ResurrectorState.IDLE)
                self._clear(eventtime)

        return eventtime + self.dump_time

    def _change_state(self, new_state):
        if self.state != new_state:
            logging.info(f"[resurrection] Change state: {self.state.name} -> {new_state.name}")
            self.state = new_state

    def _dump(self, eventtime):
        stats = self.virtual_sdcard.get_status(eventtime)

        gcode_file = stats["file_path"]

        if gcode_file and os.path.isfile(gcode_file):
            with open(self.file_path, "w") as f:
                json.dump({
                    "file_path": gcode_file,
                    "file_position": stats["file_position"],
                    "file_size": stats["file_size"],
                    "position": self.toolhead.get_status(eventtime)["position"],
                    "extruder_temp": self.extruder.get_status(eventtime)["target"],
                    "bed_temp": self.heater_bed.get_status(eventtime)["target"],
                    "mesh": self.bed_mesh.get_status(eventtime)["profile_name"],
                }, f)
        else:
            logging.info("[resurrection] Failed to save resurrection file. G-Code file is invalid")
            self._change_state(ResurrectorState.ERROR)

    def _clear(self, eventtime):
        if os.path.isfile(self.file_path):
            logging.info("[resurrection] Clear resurrection file")
            os.remove(self.file_path)

    def cmd_RESURRECT(self, gcmd):
        if self.state == ResurrectorState.RESURRECTION:
            self.gcode.run_script_from_command("_PRINT_STATUS S='RESURRECTING...'")

            self._change_state(ResurrectorState.IDLE)
            gcmd.respond_raw("// action:prompt_end")

            if not os.path.isfile(self.file_path):
                gcmd.respond_raw(f"!! The resurrection file missing!")
                return

            with open(self.file_path, "r") as f:
                try:
                    state = json.load(f)
                except Exception as e:
                    gcmd.respond_raw(f"!! Failed to resurrect. Invalid resurrection file: {str(e)}")
                    return

            for key in ["file_path", "file_position", "file_size", "position", "extruder_temp", "bed_temp", "mesh"]:
                if key not in state:
                    gcmd.respond_raw(f"!! Failed to resurrect. Missing required field: {key!r}")
                    return

            gcode_file = state["file_path"]
            if not os.path.isfile(gcode_file):
                gcmd.respond_raw(f"!! Failed to resurrect. File missing: {gcode_file!r}")
                return

            expected_file_size = state["file_size"]
            actual_file_size = os.path.getsize(gcode_file)
            if actual_file_size != expected_file_size:
                gcmd.respond_raw(f"!! Failed to resurrect. File size mismatch: {actual_file_size} <> {expected_file_size}")
                return

            mesh_name = state["mesh"]
            meshes = self.bed_mesh.get_status(self.reactor.NOW)["profiles"]
            if mesh_name not in meshes:
                if 'auto' in meshes:
                    gcmd.respond_raw(f"!! Bed mesh missing: {mesh_name!r}. Using 'auto' instead...")
                    mesh_name = 'auto'
                else:
                    gcmd.respond_raw(f"!! Failed to resurrect. Bed mesh missing: {mesh_name!r}")
                    return

            toolhead_pos = state["position"]

            self.virtual_sdcard.load_file(gcmd, os.path.basename(gcode_file))

            self.gcode.run_script_from_command("\n".join([
                f"_PRINT_STATUS S='LOADING...'",

                f"_CANCEL_DELAYED_COMMANDS",
                f"_ENSURE_SERVICES_STARTED",

                f"BED_MESH_PROFILE LOAD={mesh_name}",
                f"M26 S{state['file_position']!r}",

                f"_PRINT_STATUS S='HEATING...'",
                f"_WAIT_TEMPERATURE CMD=M140 VALUE={state['bed_temp']} BELOW=2 ABOVE=3",
                f"_WAIT_TEMPERATURE CMD=M104 VALUE={state['extruder_temp']}",

                f"_PRINT_STATUS S='HOMING...'",
                f"G28",
                f"M400",

                f"LOAD_CELL_TARE",

                f"G92 E0",  # Reset extruder position
                f"G90",  # Absolute toolhead coordinates
                f"M83",  # Relative extruder coordinates

                f"_PRINT_STATUS S='POSITIONING...'",
                f"G1 X{toolhead_pos[0]} Y{toolhead_pos[1]} F6000",
                f"G1 Z{toolhead_pos[2]} F3000",
                f"M400",

                f"_PRINT_STATUS S='PRINTING...'",
            ]))

            self.virtual_sdcard.do_resume()
            self._change_state(ResurrectorState.PRINTING)
        else:
            gcmd.respond_raw(f"!! The printer isn’t in a resurrection state!")


def load_config(config):
    return Resurrector(config)
