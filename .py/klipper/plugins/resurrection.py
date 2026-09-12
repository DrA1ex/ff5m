## Resurrection plugin implementation
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import enum, json, logging, math, os, queue, sys, threading

if __package__:
    from . import resurrection_state as _state
else:
    plugin_dir = os.path.dirname(__file__)
    if plugin_dir not in sys.path:
        sys.path.insert(0, plugin_dir)
    import resurrection_state as _state

GCodeStateParser = _state.GCodeStateParser
GCodeStateReducer = _state.GCodeStateReducer
MAX_GCODE_LINE_SIZE = _state.MAX_GCODE_LINE_SIZE
PARSE_CHUNK_SIZE = _state.PARSE_CHUNK_SIZE
RecoveryGCodeState = _state.RecoveryGCodeState
RecoveryParseCancelled = _state.RecoveryParseCancelled
RecoveryParseError = _state.RecoveryParseError
_format_number = _state._format_number

class ResurrectorState(enum.Enum):
    UNKNOWN = 0

    IDLE = 10
    RESURRECTION = 20
    LOADING = 21
    PREPARING = 22

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

        self.dump_time = config.getfloat("dump_time", 3., minval=1.)
        self.file_path = config.get("filename")
        self.enabled = False
        self._pause_checkpoint_active = False
        self._resume_pending = False
        self._checkpoint_cache = None
        self._checkpoint_cache_loaded = False
        self._worker = None
        self._worker_cancel = None
        self._timer = None

        self.printer.register_event_handler("klippy:ready", self._init)

        self.gcode.register_command("RESURRECT", self.cmd_RESURRECT)
        self.gcode.register_command("RESURRECT_ABORT", self.cmd_RESURRECT_ABORT)
        self.gcode.register_command(
            "_RESURRECTION_PAUSE", self.cmd_RESURRECTION_PAUSE)
        self.gcode.register_command(
            "_RESURRECTION_RESUME", self.cmd_RESURRECTION_RESUME)

    def get_status(self, eventtime):
        """Return a small, path-safe summary for local displays.

        The full recovery payload contains an absolute path and toolhead
        coordinates.  Neither is part of the public status contract.
        """
        result = {
            "state": self.state.name.lower(),
            "available": self.state == ResurrectorState.RESURRECTION,
            "supports_pause_markers": True,
            "filename": "",
            "progress": 0.0,
            "extruder_target": 0.0,
            "bed_target": 0.0,
            "mesh": "",
        }
        if not result["available"] or not os.path.isfile(self.file_path):
            return result
        try:
            if not getattr(self, "_checkpoint_cache_loaded", False):
                with open(self.file_path, "r") as stream:
                    self._checkpoint_cache = json.load(stream)
                self._checkpoint_cache_loaded = True
            saved = self._checkpoint_cache
            file_size = max(0, int(saved.get("file_size", 0)))
            file_position = max(0, int(saved.get("file_position", 0)))
            result.update({
                "filename": os.path.basename(str(saved.get("file_path", ""))),
                "progress": (min(file_position, file_size) / float(file_size)
                             if file_size else 0.0),
                "extruder_target": float(saved.get("extruder_temp", 0.0)),
                "bed_target": float(saved.get("bed_temp", 0.0)),
                "mesh": str(saved.get("mesh", "")),
            })
        except (AttributeError, ValueError, TypeError, IOError, OSError):
            logging.exception("[resurrection] Unable to publish recovery status")
            result["available"] = False
            result["state"] = "error"
        return result

    def _init(self):
        self.mod_params = self.printer.lookup_object("mod_params")

        if not self.mod_params.variables['power_loss_recovery']:
            logging.info("[resurrection] Disabled due to 'power_loss_recovery' parameter.")
            return

        self.printer.register_event_handler("klippy:shutdown", self._shutdown)
        self.printer.register_event_handler("klippy:disconnect", self._disconnect)

        self.toolhead = self.printer.lookup_object("toolhead")
        self.virtual_sdcard = self.printer.lookup_object("virtual_sdcard")
        self.print_stats = self.printer.lookup_object("print_stats")

        self.extruder = self.printer.lookup_object("extruder")
        self.heater_bed = self.printer.lookup_object("heater_bed")
        self.bed_mesh = self.printer.lookup_object("bed_mesh")
        self.gcode_move = self.printer.lookup_object("gcode_move")

        self.start_print_macro = self.printer.lookup_object('gcode_macro _START_PRINT')
        self.enabled = True

        if os.path.isfile(self.file_path):
            logging.info("[resurrection] Resurrection file exists.")
            self._change_state(ResurrectorState.RESURRECTION)

            def _initial_msg(_):
                if self.state != ResurrectorState.RESURRECTION:
                    return
                self.gcode.respond_raw("// action:prompt_begin Resurrection")
                self.gcode.respond_raw("// action:prompt_text Resurrection is available! Would you like to restore the print?")
                self.gcode.respond_raw("// action:prompt_footer_button Restore|RESURRECT")
                self.gcode.respond_raw("// action:prompt_footer_button Cleanup|RESURRECT_ABORT")
                self.gcode.respond_raw("// action:prompt_footer_button Later|RESPOND TYPE=command MSG=action:prompt_end")
                self.gcode.respond_raw("// action:prompt_show")

                self.gcode.respond_raw("// Resurrection is available!")
                self.gcode.respond_raw("// Run RESURRECT to restore the print")
                self.gcode.respond_raw("// Run RESURRECT_ABORT to perform cleanup")

            self.reactor.register_callback(_initial_msg, waketime=self.reactor.monotonic() + 3)
        else:
            logging.info("[resurrection] Resurrection file doesn't exist")
            self._change_state(ResurrectorState.IDLE)

        self._timer = self.reactor.register_timer(self._dump_timer_handler, self.reactor.NOW)

    def _disconnect(self):
        logging.info("[resurrection] Disconnect...")
        self._cancel_worker()
        if self._timer:
            self.reactor.unregister_timer(self._timer)
            self._timer = None

        self._change_state(ResurrectorState.DESTROYED)

    def _shutdown(self):
        logging.info("[resurrection] Shutdown...")
        if self.state == ResurrectorState.PRINTING:
            self._dump(self.reactor.monotonic())

        self._disconnect()

    def _dump_timer_handler(self, eventtime):
        if self.state == ResurrectorState.DESTROYED:
            return

        stats = self.print_stats.get_status(eventtime)
        stats_state = stats["state"]

        if (stats_state == "printing"
                and self.start_print_macro.variables["print_started"]
                and not self._pause_checkpoint_active):
            self._resume_pending = False
            self._change_state(ResurrectorState.PRINTING)
        elif (stats_state in {"complete", "cancelled"}
              and self.state in {
                  ResurrectorState.PRINTING,
                  ResurrectorState.PAUSED,
                  ResurrectorState.ERROR,
              }):
            self._pause_checkpoint_active = False
            self._resume_pending = False
            self._change_state(ResurrectorState.IDLE)
            self._clear(eventtime)

        if self.state == ResurrectorState.PRINTING:
            if stats_state == "printing":
                self._dump(eventtime)
            elif stats_state == "paused":
                if self._resume_pending:
                    return eventtime + self.dump_time
                # Normally _RESURRECTION_PAUSE has already saved the exact
                # pre-park position. If a custom PAUSE macro omitted the
                # marker, keep the last periodic checkpoint instead of
                # overwriting it with the parked toolhead coordinates.
                self._pause_checkpoint_active = True
                self._resume_pending = False
                self._change_state(ResurrectorState.PAUSED)
            elif stats_state == "error":
                self._pause_checkpoint_active = False
                self._resume_pending = False
                self._change_state(ResurrectorState.IDLE)
                self._dump(eventtime)
            elif stats_state == "idle":
                self._pause_checkpoint_active = False
                self._resume_pending = False
                self._change_state(ResurrectorState.IDLE)
                self._clear(eventtime)

        return eventtime + self.dump_time

    def _change_state(self, new_state):
        if self.state != new_state:
            logging.info(f"[resurrection] Change state: {self.state.name} -> {new_state.name}")
            self.state = new_state

    def _print_has_started(self):
        return bool(self.start_print_macro.variables["print_started"])

    def cmd_RESURRECTION_PAUSE(self, gcmd):
        if (not self.enabled or self.state == ResurrectorState.DESTROYED
                or self._pause_checkpoint_active
                or not self._print_has_started()):
            return
        eventtime = self.reactor.monotonic()
        stats_state = self.print_stats.get_status(eventtime)["state"]
        if stats_state != "printing":
            return

        # Freeze checkpoint updates before PAUSE_BASE captures state and the
        # surrounding macro parks the toolhead.  For a PAUSE command coming
        # from the SD file, _dump() uses next_file_position and therefore
        # resumes at the following line instead of pausing again.
        self._dump(eventtime)
        self._pause_checkpoint_active = True
        self._resume_pending = False
        self._change_state(ResurrectorState.PAUSED)

    def cmd_RESURRECTION_RESUME(self, gcmd):
        if (not self.enabled or self.state == ResurrectorState.DESTROYED
                or not self._pause_checkpoint_active):
            return
        if not self._print_has_started():
            self._pause_checkpoint_active = False
            self._resume_pending = False
            return

        # pause_resume restores PAUSE_STATE before virtual SD is resumed.
        # Save that restored position before scheduling the next file line,
        # then allow periodic checkpoints again.
        eventtime = self.reactor.monotonic()
        self._pause_checkpoint_active = False
        self._resume_pending = True
        self._change_state(ResurrectorState.PRINTING)
        self._dump(eventtime)

    def _cancel_worker(self):
        worker = getattr(self, "_worker", None)
        cancel_event = getattr(self, "_worker_cancel", None)
        if worker is None:
            return
        if cancel_event is not None:
            cancel_event.set()
        if worker is not threading.current_thread():
            worker.join()
        if self._worker is worker:
            self._worker = None
            self._worker_cancel = None

    def _dump(self, eventtime):
        stats = self.virtual_sdcard.get_status(eventtime)
        gcode_file = stats["file_path"]

        if gcode_file and os.path.isfile(gcode_file):
            file_position = stats["file_position"]
            if self.virtual_sdcard.is_cmd_from_sd():
                file_position = self.virtual_sdcard.get_file_position()
            t_status = self.toolhead.get_status(eventtime)
            position = t_status["position"]

            extruder_temp = self.extruder.get_status(eventtime)["target"]
            bed_temp = self.heater_bed.get_status(eventtime)["target"]
            mesh = self.bed_mesh.get_status(eventtime)["profile_name"]
            z_offset = self.gcode_move.get_status(eventtime)["homing_origin"][2]

            if extruder_temp == 0:
                logging.info("[resurrection] Skip dump due to zeroed extruder temp")
                return

            checkpoint = {
                "file_path": gcode_file,
                "file_position": file_position,
                "file_size": stats["file_size"],
                "position": position,
                "extruder_temp": extruder_temp,
                "z_offset": z_offset,
                "bed_temp": bed_temp,
                "mesh": mesh,
            }
            temporary_path = self.file_path + ".tmp"
            try:
                with open(temporary_path, "w") as stream:
                    json.dump(checkpoint, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary_path, self.file_path)
                self._checkpoint_cache = checkpoint
                self._checkpoint_cache_loaded = True
            except (IOError, OSError) as e:
                logging.error(f"[resurrection] Failed to save resurrection file: {e}")
                try:
                    if os.path.isfile(temporary_path):
                        os.remove(temporary_path)
                except OSError:
                    logging.exception(
                        "[resurrection] Failed to remove temporary checkpoint")
                return
        else:
            logging.info("[resurrection] Failed to save resurrection file. G-Code file is invalid")
            self._change_state(ResurrectorState.ERROR)

    def _clear(self, eventtime):
        if os.path.isfile(self.file_path):
            logging.info("[resurrection] Clear resurrection file")
            try:
                os.remove(self.file_path)
            except (IOError, OSError) as e:
                logging.error(f"[resurrection] Failed to remove resurrection file: {e}")
                return
        self._checkpoint_cache = None
        self._checkpoint_cache_loaded = True

    def _load_resurrection_state(self, gcmd):
        if not os.path.isfile(self.file_path):
            gcmd.respond_raw("!! The resurrection file missing!")
            return None

        try:
            with open(self.file_path, "r") as stream:
                state = json.load(stream)
        except (ValueError, IOError, OSError) as e:
            gcmd.respond_raw(
                f"!! Failed to resurrect. Invalid resurrection file: {str(e)}")
            return None

        required = [
            "file_path", "file_position", "file_size", "position", "z_offset",
            "extruder_temp", "bed_temp", "mesh",
        ]
        for key in required:
            if key not in state:
                gcmd.respond_raw(f"!! Failed to resurrect. Missing required field: {key!r}")
                return None

        gcode_file = state["file_path"]
        if not isinstance(gcode_file, str) or not os.path.isfile(gcode_file):
            gcmd.respond_raw(f"!! Failed to resurrect. File missing: {gcode_file!r}")
            return None

        expected_file_size = state["file_size"]
        file_position = state["file_position"]
        if (isinstance(expected_file_size, bool)
                or not isinstance(expected_file_size, int)
                or expected_file_size < 0
                or isinstance(file_position, bool)
                or not isinstance(file_position, int)
                or file_position < 0
                or file_position > expected_file_size):
            gcmd.respond_raw(
                "!! Failed to resurrect. Invalid file size or position")
            return None

        position = state["position"]
        if not isinstance(position, (list, tuple)) or len(position) < 4:
            gcmd.respond_raw("!! Failed to resurrect. Invalid toolhead position")
            return None
        if not isinstance(state["mesh"], str):
            gcmd.respond_raw("!! Failed to resurrect. Invalid mesh name")
            return None
        numeric_values = list(position[:4]) + [
            state["z_offset"], state["extruder_temp"], state["bed_temp"]]
        try:
            if not all(math.isfinite(float(value)) for value in numeric_values):
                raise ValueError
        except (TypeError, ValueError):
            gcmd.respond_raw(
                "!! Failed to resurrect. Invalid numeric checkpoint value")
            return None

        expected_file_size = state["file_size"]
        actual_file_size = os.path.getsize(gcode_file)
        if actual_file_size != expected_file_size:
            gcmd.respond_raw(f"!! Failed to resurrect. File size mismatch: {actual_file_size} <> {expected_file_size}")
            return None

        root = os.path.realpath(self.virtual_sdcard.sdcard_dirname)
        real_file = os.path.realpath(gcode_file)
        try:
            inside_sdcard = os.path.commonpath([root, real_file]) == root
        except ValueError:
            inside_sdcard = False
        if not inside_sdcard:
            gcmd.respond_raw(
                "!! Failed to resurrect. G-Code file is outside virtual SD")
            return None

        if file_position:
            try:
                with open(gcode_file, "rb") as stream:
                    stream.seek(file_position - 1)
                    valid_boundary = stream.read(1) == b"\n"
            except (IOError, OSError) as e:
                gcmd.respond_raw(
                    f"!! Failed to resurrect. Unable to read G-Code: {e}")
                return None
            if not valid_boundary:
                gcmd.respond_raw(
                    "!! Failed to resurrect. File position is not a line boundary")
                return None

        state["_relative_path"] = os.path.relpath(real_file, root)
        self._checkpoint_cache = dict(state)
        self._checkpoint_cache.pop("_relative_path", None)
        self._checkpoint_cache_loaded = True
        return state

    def _load_state(self, stats):
        cancel_event = threading.Event()
        results = queue.Queue(maxsize=1)
        parser = GCodeStateParser(
            stats["file_path"], stats["file_position"],
            stats["file_size"], cancel_event)

        def _worker():
            try:
                results.put((True, parser.parse()))
            except Exception as error:
                results.put((False, error))

        worker = threading.Thread(
            target=_worker, name="resurrection-gcode-parser")
        self._worker = worker
        self._worker_cancel = cancel_event
        worker.start()
        try:
            while worker.is_alive():
                self.reactor.pause(self.reactor.monotonic() + .050)
                if self.state == ResurrectorState.DESTROYED:
                    cancel_event.set()
            worker.join()
            if results.empty():
                raise RecoveryParseError(
                    "G-Code state parser stopped without a result")
            success, result = results.get_nowait()
            if not success:
                raise result
            return result
        finally:
            if worker.is_alive():
                cancel_event.set()
                worker.join()
            if self._worker is worker:
                self._worker = None
                self._worker_cancel = None

    def _rollback_recovery(self, gcmd, message, file_loaded=False):
        logging.error("[resurrection] Recovery failed: %s", message)
        self._cancel_worker()
        if self.state != ResurrectorState.DESTROYED:
            self._change_state(ResurrectorState.RESURRECTION)
        if file_loaded:
            try:
                self.virtual_sdcard.do_cancel()
            except Exception:
                logging.exception(
                    "[resurrection] Failed to reset virtual SD after recovery")
        if self.state != ResurrectorState.DESTROYED:
            try:
                self.gcode.run_script_from_command(
                    "_CONTEXT_RESET\n"
                    "TURN_OFF_HEATERS\n"
                    "M106 P1 S0")
            except Exception:
                logging.exception(
                    "[resurrection] Failed to apply recovery cleanup")
        gcmd.respond_raw("!! Failed to resurrect. %s" % (message,))

    def _restore_physical_position(self, position):
        # Checkpoints contain the final toolhead coordinates after gcode
        # offset, bed mesh, and skew transforms.  Moving through G-Code would
        # apply those transforms a second time.  A manual toolhead move uses
        # machine coordinates and emits toolhead:manual_move, which makes
        # gcode_move invert the active transforms and synchronize its logical
        # position before virtual SD processing resumes.
        self.toolhead.manual_move(
            [position[0], position[1], None], 100.)
        self.toolhead.manual_move(
            [None, None, position[2]], 50.)
        self.toolhead.wait_moves()
        restored = self.toolhead.get_position()
        if any(abs(restored[axis] - position[axis]) > 1.e-6
               for axis in range(3)):
            raise RecoveryParseError(
                "Toolhead did not reach the checkpoint position")

    def cmd_RESURRECT(self, gcmd):
        if self.state != ResurrectorState.RESURRECTION:
            gcmd.respond_raw(f"!! The printer isn’t in a resurrection state!")
            return

        self.gcode.run_script_from_command("\n".join([
            '_CONTEXT_BEGIN TYPE=recovery',
            '_CONTEXT_STATE NAME="LOADING STATE"',
        ]))
        gcmd.respond_raw("// action:prompt_end")

        state = self._load_resurrection_state(gcmd)
        if state is None:
            self.gcode.run_script_from_command("_CONTEXT_RESET")
            return

        mesh_name = state["mesh"]
        meshes = self.bed_mesh.get_status(self.reactor.monotonic())["profiles"]
        if mesh_name not in meshes:
            if 'auto' in meshes:
                gcmd.respond_raw(f"!! Bed mesh missing: {mesh_name!r}. Using 'auto' instead...")
                mesh_name = 'auto'
            else:
                gcmd.respond_raw(f"!! Failed to resurrect. Bed mesh missing: {mesh_name!r}")
                self.gcode.run_script_from_command("_CONTEXT_RESET")
                return

        self._change_state(ResurrectorState.LOADING)
        file_loaded = False
        try:
            parsed_state = self._load_state(state)
            if self.state == ResurrectorState.DESTROYED:
                return

            self.virtual_sdcard.load_file(gcmd, state["_relative_path"])
            file_loaded = True
            self._change_state(ResurrectorState.PREPARING)

            toolhead_pos = [float(value) for value in state["position"][:4]]
            bed_temp = float(state["bed_temp"])
            extruder_temp = float(state["extruder_temp"])
            z_offset = float(state["z_offset"])
            self.gcode.run_script_from_command("\n".join([
                "_CONTEXT_STATE NAME=PREPARING",
                "_START_PRINT_PREPARE",
                f"BED_MESH_PROFILE LOAD={mesh_name}",
                f"M26 S{state['file_position']}",
                "_WAIT_TEMPERATURE CMD=M140 VALUE=%s BELOW=2 ABOVE=3"
                % (_format_number(bed_temp),),
                "M106 P1 S255",
                "_WAIT_TEMPERATURE CMD=M104 VALUE=%s"
                % (_format_number(extruder_temp),),
                "_HOME_IF_NEEDED",
                "M400",
                "LOAD_CELL_TARE",
                "G92 E0",
                "G90",
                "M83",
                "_CONTEXT_STATE NAME=POSITIONING",
                "_SET_GCODE_OFFSET Z=%s" % (_format_number(z_offset),),
            ]))

            skew_commands = parsed_state.skew_commands()
            if skew_commands:
                self.gcode.run_script_from_command("\n".join(skew_commands))

            self._restore_physical_position(toolhead_pos)
            self.gcode.run_script_from_command("\n".join([
                "M106 P1 S0",
                '_CONTEXT_STATE NAME="RESTORING STATE"',
            ]))

            self.gcode.run_script_from_command("\n".join(
                parsed_state.before_retraction_commands(
                    include_skew=False)))

            if parsed_state.has_retraction_state:
                firmware_retraction = self.printer.lookup_object(
                    "firmware_retraction", None)
                if firmware_retraction is None:
                    raise RecoveryParseError(
                        "Firmware retraction state can not be restored")
                firmware_retraction.is_retracted = parsed_state.retracted

            final_commands = parsed_state.final_commands()
            final_commands.extend([
                "_CONTEXT_END",
                "SET_GCODE_VARIABLE MACRO=_START_PRINT "
                "VARIABLE=print_active VALUE=True",
                "SET_GCODE_VARIABLE MACRO=_START_PRINT "
                "VARIABLE=print_started VALUE=True",
                "_CONTEXT_BEGIN TYPE=print",
                "_CONTEXT_STATE NAME=PRINTING",
            ])
            self.gcode.run_script_from_command("\n".join(final_commands))

            self.virtual_sdcard.do_resume()
            self._change_state(ResurrectorState.PRINTING)
            self.gcode.respond_raw("// Resurrection finished!")
        except Exception as error:
            if self.state != ResurrectorState.DESTROYED:
                self._rollback_recovery(
                    gcmd, str(error) or error.__class__.__name__, file_loaded)

    def cmd_RESURRECT_ABORT(self, gcmd):
        if self.state != ResurrectorState.RESURRECTION:
            gcmd.respond_raw(f"!! The printer isn’t in a resurrection state!")
            return

        self.gcode.run_script_from_command("\n".join([
            '_CONTEXT_BEGIN TYPE=recovery',
            '_CONTEXT_STATE NAME="LOADING STATE"',
        ]))
        gcmd.respond_raw("// action:prompt_end")
        state = self._load_resurrection_state(gcmd)
        if state is None:
            self.gcode.run_script_from_command("_CONTEXT_RESET")
            return

        try:
            self.gcode.run_script_from_command("\n".join([
                '_CONTEXT_STATE NAME=PREPARING',
                f"_WAIT_TEMPERATURE CMD=M140 VALUE={state['bed_temp']} BELOW=2 ABOVE=3",
                f"_WAIT_TEMPERATURE CMD=M104 VALUE={state['extruder_temp']}",

                "_HOME_IF_NEEDED",
                f"M400",

                "_CONTEXT_STATE NAME=FINISHING",
                f"TURN_OFF_HEATERS",
                f"_CONTEXT_END",
            ]))
        except Exception as error:
            if self.state != ResurrectorState.DESTROYED:
                self._rollback_recovery(
                    gcmd, str(error) or error.__class__.__name__)
            return

        self._clear(self.reactor.monotonic())
        self._change_state(ResurrectorState.IDLE)

        self.gcode.respond_raw("// Resurrection aborted!")


def load_config(config):
    return Resurrector(config)
