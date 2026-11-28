## Resurrection plugin implementation
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import enum, json, logging, math, os

from .recovery.state import RecoveryParseError, _format_number
from .object_exclusion import object_name_parameter
from .recovery import companion, snapshot

class ResurrectorState(enum.Enum):
    UNKNOWN = 0

    IDLE = 10
    RESURRECTION = 20
    LOADING = 21
    PREPARING = 22

    PRINTING = 30
    PAUSED = 40

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
        self._checkpoint_cache = None
        self._checkpoint_cache_loaded = False
        self._objects_key = None
        self._objects_ref = None
        self._recovery_active = False
        self._legacy_loader = None
        self.last_error = None
        self._timer = None

        self.printer.register_event_handler("klippy:ready", self._init)
        self.printer.register_event_handler(
            "virtual_sdcard:reset_file", self._handle_virtual_sd_reset)

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
        restored = (self._recovery_active
                    and self.print_stats.get_status(eventtime).get("state")
                    in {"printing", "paused"})

        result = {
            "state": self.state.name.lower(),
            "available": self.state == ResurrectorState.RESURRECTION,
            "restored": restored,
            "supports_pause_markers": True,
            "filename": "",
            "progress": 0.0,
            "extruder_target": 0.0,
            "bed_target": 0.0,
            "mesh": "",
        }
        if self.state == ResurrectorState.LOADING:
            loader = self._legacy_loader
            result["parse_progress"] = loader.progress if loader is not None else None
        if not result["available"]:
            return result
        if not os.path.isfile(self.file_path):
            result["available"] = False
            return result
        try:
            if not self._checkpoint_cache_loaded:
                with open(self.file_path, "r", encoding="utf-8") as stream:
                    self._checkpoint_cache = json.load(stream)
                self._checkpoint_cache_loaded = True
            saved = self._checkpoint_cache
            if not isinstance(saved, dict):
                raise ValueError("Invalid checkpoint")
            # File metadata is informational here: cleanup does not need it.
            file_size = saved.get("file_size")
            file_position = saved.get("file_position")
            progress = 0.0
            if (type(file_size) is int and file_size > 0
                    and type(file_position) is int and file_position >= 0):
                progress = min(file_position, file_size) / file_size
            extruder_target = float(saved.get("extruder_temp", 0.0))
            bed_target = float(saved.get("bed_temp", 0.0))
            if not (math.isfinite(extruder_target) and math.isfinite(bed_target)):
                raise ValueError("Invalid checkpoint temperature")
            filename = saved.get("file_path")
            mesh = saved.get("mesh")
            result.update({
                "filename": os.path.basename(filename) if isinstance(filename, str) else "",
                "progress": progress,
                "extruder_target": extruder_target,
                "bed_target": bed_target,
                "mesh": mesh if isinstance(mesh, str) else "",
            })
        except (AttributeError, ValueError, TypeError, OverflowError, OSError):
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
        self.printer.register_event_handler(
            "virtual_sdcard:print_start", self._handle_print_start)
        self.printer.register_event_handler(
            "virtual_sdcard:command_boundary", self._handle_command_boundary)

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
        if self._legacy_loader is not None:
            self._legacy_loader.cancel()
        if self._timer:
            self.reactor.unregister_timer(self._timer)
            self._timer = None

        self._change_state(ResurrectorState.DESTROYED)

    def _shutdown(self):
        logging.info("[resurrection] Shutdown...")
        try:
            eventtime = self.reactor.monotonic()
            # A yielding command can have partially updated live state.
            if (self.state == ResurrectorState.PRINTING and not self._parked()
                    and self.virtual_sdcard.is_active()
                    and not self.virtual_sdcard.is_cmd_from_sd()
                    and not self.gcode.get_mutex().test()
                    and self.print_stats.get_status(eventtime)["state"] == "printing"):
                self._dump(eventtime)
            elif self.state == ResurrectorState.PAUSED:
                self._refresh_paused_exclusions(eventtime)
        finally:
            self._disconnect()

    def _dump_timer_handler(self, eventtime):
        if self.state == ResurrectorState.DESTROYED:
            return

        stats_state = self.print_stats.get_status(eventtime)["state"]
        if self.state in {ResurrectorState.IDLE, ResurrectorState.PRINTING,
                          ResurrectorState.PAUSED}:
            if (stats_state == "printing" and self._print_has_started()
                    and self.virtual_sdcard.is_active() and not self._parked()):
                self._change_state(ResurrectorState.PRINTING)
                self.virtual_sdcard.request_command_boundary()
            elif self.state in {ResurrectorState.PRINTING, ResurrectorState.PAUSED}:
                if stats_state in {"complete", "cancelled", "standby", "idle"}:
                    self._change_state(ResurrectorState.IDLE)
                    self._clear(eventtime)
                elif stats_state == "error":
                    # The failed line may have partially modified live state.
                    self._change_state(ResurrectorState.IDLE)
                elif (stats_state == "paused"
                      and self.state == ResurrectorState.PRINTING
                      and not self.virtual_sdcard.is_active()):
                    # A custom or direct SD pause may omit the marker.
                    # During resume the SD worker is already scheduled, even
                    # if print_stats has not yet changed back to printing.
                    self._change_state(ResurrectorState.PAUSED)

        if self.state == ResurrectorState.PAUSED:
            self._refresh_paused_exclusions(eventtime)
        return eventtime + self.dump_time

    def _handle_command_boundary(self, eventtime):
        # Virtual SD owns the one-shot notification request.
        if (self.state != ResurrectorState.PRINTING
                or not self._print_has_started() or self._parked()
                or self.print_stats.get_status(eventtime)["state"] != "printing"):
            return
        try:
            self._dump(eventtime)
        except Exception:
            # Recovery is optional; a failed snapshot must not interrupt SD.
            logging.exception("[resurrection] Checkpoint boundary save failed")

    def _handle_print_start(self):
        # RESURRECT deliberately loads a file and resumes from a nonzero
        # position. Its existing checkpoint must survive preparation errors.
        if self._recovery_active:
            return
        # Recovery is optional: log a deletion failure, as before, and
        # let the new print proceed. _clear retains the referenced companion
        # if the main file could not be removed.
        self._clear(self.reactor.monotonic())
        # A failed previous print may have left this flag set. Only this
        # job's completed preparation may enable checkpointing.
        if self._print_has_started():
            self.gcode.run_script_from_command(
                "SET_GCODE_VARIABLE MACRO=_START_PRINT VARIABLE=print_started VALUE=False")
        self._change_state(ResurrectorState.IDLE)

    def _parked(self):
        pause = self.printer.lookup_object("pause_resume", None)
        return pause is not None and pause.is_paused

    def _change_state(self, new_state):
        if new_state in {
                ResurrectorState.IDLE,
                ResurrectorState.RESURRECTION,
                ResurrectorState.DESTROYED,
        }:
            self._recovery_active = False
        if self.state != new_state:
            logging.info(f"[resurrection] Change state: {self.state.name} -> {new_state.name}")
            self.state = new_state

    def _handle_virtual_sd_reset(self):
        self._recovery_active = False
        # The old checkpoint still owns its companion until a new one commits.
        self._objects_key = self._objects_ref = None

    def _print_has_started(self):
        return bool(self.start_print_macro.variables["print_started"])

    def cmd_RESURRECTION_PAUSE(self, gcmd):
        if (not self.enabled or self.state == ResurrectorState.DESTROYED
                or not self._print_has_started()):
            return
        eventtime = self.reactor.monotonic()
        stats_state = self.print_stats.get_status(eventtime)["state"]
        if stats_state not in {"printing", "paused"}:
            return

        # Freeze the pre-park checkpoint, even if the timer already observed PAUSE_BASE.
        self._change_state(ResurrectorState.PAUSED)

        try:
            # PAUSE_BASE has stopped SD. Capture the commanded endpoint
            # before parking; a nested SD PAUSE resumes after its own line.
            position = (self.virtual_sdcard.get_file_position()
                        if self.virtual_sdcard.is_cmd_from_sd() else None)
            self._dump(eventtime, position)
        except Exception:
            logging.exception("[resurrection] Unable to save pre-park checkpoint")

    def cmd_RESURRECTION_RESUME(self, gcmd):
        if (not self.enabled or self.state != ResurrectorState.PAUSED
                or not self._print_has_started()):
            return

        # Save only after the next completed SD command, not during RESUME.
        self._change_state(ResurrectorState.PRINTING)
        if self._timer is not None:
            self.reactor.update_timer(self._timer, self.reactor.NOW)

    def _save_objects(self):
        previous = getattr(self, "_objects_key", None)
        key, data = companion.capture(self.printer, self.bed_mesh, previous)
        if data is None: return

        # A restored job has new live object identities, but may still own
        # the same immutable companion. Compare content only on its first save.
        reference = None
        if previous is None and getattr(self, "_recovery_active", False):
            reference = (self._checkpoint_cache or {}).get("objects_ref")
            if reference is not None:
                try:
                    if companion.read(self.file_path, reference) != data:
                        reference = None
                except RecoveryParseError:
                    reference = None

        self._objects_ref = reference or companion.write(self.file_path, data)
        self._objects_key = key

    def _dump(self, eventtime, file_position=None):
        stats = self.virtual_sdcard.get_status(eventtime)
        gcode_file = stats["file_path"]

        if gcode_file:
            if file_position is None:
                file_position = stats["file_position"]
            t_status = self.toolhead.get_status(eventtime)
            position = t_status["position"]
            e_status = self.extruder.get_status(eventtime)
            m_status = self.gcode_move.get_status(eventtime)

            extruder_temp = e_status["target"]
            bed_temp = self.heater_bed.get_status(eventtime)["target"]
            mesh = self.bed_mesh.get_status(eventtime)["profile_name"]

            if extruder_temp == 0:
                logging.info("[resurrection] Skip dump due to zeroed extruder temp")
                return

            try:
                live_state, excluded = snapshot.capture(
                    self.printer, m_status, t_status, e_status,
                    self.print_stats, eventtime)
                self._save_objects()
            except (AttributeError, KeyError, TypeError, ValueError, OSError, RecoveryParseError):
                logging.exception("[resurrection] Unable to capture checkpoint state")
                return

            checkpoint = {
                "version": snapshot.VERSION,
                "file_mtime_ns": stats["file_mtime_ns"],
                "file_path": gcode_file,
                "file_position": file_position,
                "file_size": stats["file_size"],
                "position": position,
                "extruder_temp": extruder_temp,
                "bed_temp": bed_temp,
                "mesh": mesh,
            }
            checkpoint.update(snapshot.encode(live_state))
            checkpoint["objects_ref"] = self._objects_ref
            if excluded:
                checkpoint["excluded_objects"] = excluded
            self._write_checkpoint(checkpoint)

    def _excluded_objects(self, eventtime):
        source = self.printer.lookup_object("exclude_object", None)
        return sorted(set(source.get_status(eventtime)["excluded_objects"])) if source is not None else []

    def _refresh_paused_exclusions(self, eventtime):
        saved = self._checkpoint_cache
        if saved is None or self.virtual_sdcard.get_status(eventtime)["file_path"] != saved["file_path"]:
            return
        excluded = self._excluded_objects(eventtime)
        if excluded == saved.get("excluded_objects", []):
            return
        checkpoint = dict(saved)
        checkpoint.pop("excluded_objects", None)
        if excluded:
            checkpoint["excluded_objects"] = excluded
        self._write_checkpoint(checkpoint)

    def _write_checkpoint(self, checkpoint):
        previous = getattr(self, "_checkpoint_cache", None)
        old_ref = previous.get("objects_ref") if isinstance(previous, dict) else None
        new_ref = checkpoint.get("objects_ref")
        temporary_path = self.file_path + ".tmp"
        try:
            payload = json.dumps(checkpoint, separators=(",", ":"), allow_nan=False)
            with open(temporary_path, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self.file_path)
            self._checkpoint_cache = checkpoint
            self._checkpoint_cache_loaded = True
            if new_ref != old_ref:
                try:
                    companion.prune(self.file_path, new_ref)
                except OSError:
                    # Main checkpoint already committed. Keep unused objects
                    # rather than making recovery depend on successful cleanup.
                    logging.exception("[resurrection] Unable to prune recovery objects")
            return True
        except (IOError, OSError, ValueError, TypeError) as e:
            logging.error(f"[resurrection] Failed to save resurrection file: {e}")
            try:
                if os.path.isfile(temporary_path):
                    os.remove(temporary_path)
            except OSError:
                logging.exception("[resurrection] Failed to remove temporary checkpoint")
            return False

    def _clear(self, eventtime):
        try:
            os.remove(self.file_path)
            logging.info("[resurrection] Cleared resurrection file")
        except FileNotFoundError:
            pass
        except OSError as error:
            logging.error("[resurrection] Failed to remove resurrection file: %s", error)
            return error

        self._checkpoint_cache = None
        self._checkpoint_cache_loaded = True
        self._objects_key = self._objects_ref = None
        try:
            companion.prune(self.file_path)
        except OSError:
            logging.exception("[resurrection] Unable to persist recovery cleanup")

    def _report_recovery_error(self, gcmd, message):
        self.last_error = message
        logging.error("[resurrection] %s", message)
        gcmd.respond_raw("!! " + message)

    def _load_resurrection_state(self, gcmd, cleanup=False):
        if not os.path.isfile(self.file_path):
            self._report_recovery_error(gcmd, "The resurrection file missing!")
            self._change_state(ResurrectorState.IDLE)
            return None

        try:
            with open(self.file_path, "r", encoding="utf-8") as stream:
                state = json.load(stream)
        except (ValueError, IOError, OSError) as e:
            self._report_recovery_error(
                gcmd, f"Failed to resurrect. Invalid resurrection file: {e}")
            return None

        if not isinstance(state, dict):
            self._report_recovery_error(
                gcmd, "Failed to resurrect. Expected a JSON object")
            return None

        required = ["extruder_temp", "bed_temp"]
        if not cleanup:
            required += ["file_path", "file_position", "file_size",
                         "position", "mesh"]
        for key in required:
            if key not in state:
                self._report_recovery_error(
                    gcmd, f"Failed to resurrect. Missing required field: {key!r}")
                return None

        for key in ("extruder_temp", "bed_temp"):
            try:
                value = state[key]
                if isinstance(value, bool):
                    raise ValueError
                value = float(value)
                if not math.isfinite(value) or value < 0:
                    raise ValueError
                state[key] = value
            except (TypeError, ValueError, OverflowError):
                self._report_recovery_error(
                    gcmd, f"Failed to resurrect. Invalid checkpoint temperature: {key}")
                return None

        # Cleanup only heats and homes. The original job and its parsing
        # metadata are not needed to safely abandon it.
        if cleanup:
            return state

        excluded = state.get("excluded_objects", [])
        try:
            if not isinstance(excluded, list):
                raise RuntimeError("Expected a list of object names")
            for name in excluded:
                object_name_parameter(name)
        except RuntimeError as error:
            self._report_recovery_error(
                gcmd, "Failed to resurrect. Invalid excluded objects: %s" % error)
            return None

        gcode_file = state["file_path"]
        if not isinstance(gcode_file, str) or not os.path.isfile(gcode_file):
            self._report_recovery_error(
                gcmd, f"Failed to resurrect. File missing: {gcode_file!r}")
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
            self._report_recovery_error(
                gcmd, "Failed to resurrect. Invalid file size or position")
            return None

        position = state["position"]
        if not isinstance(position, (list, tuple)) or len(position) < 4:
            self._report_recovery_error(
                gcmd, "Failed to resurrect. Invalid toolhead position")
            return None
        if not isinstance(state["mesh"], str):
            self._report_recovery_error(
                gcmd, "Failed to resurrect. Invalid mesh name")
            return None
        try:
            if not all(not isinstance(value, bool) and math.isfinite(float(value))
                       for value in position[:4]):
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            self._report_recovery_error(
                gcmd, "Failed to resurrect. Invalid numeric checkpoint value")
            return None

        try:
            file_stat = os.stat(gcode_file)
            actual_file_size = file_stat.st_size
        except OSError as error:
            self._report_recovery_error(
                gcmd, f"Failed to resurrect. Unable to read G-Code: {error}")
            return None
        if actual_file_size != expected_file_size:
            self._report_recovery_error(
                gcmd, f"Failed to resurrect. File size mismatch: {actual_file_size} <> {expected_file_size}")
            return None

        if "version" in state:
            if type(state["version"]) is not int or state["version"] != snapshot.VERSION:
                self._report_recovery_error(gcmd, "Unsupported checkpoint version")
                return None
            if (type(state.get("file_mtime_ns")) is not int
                    or state["file_mtime_ns"] != file_stat.st_mtime_ns):
                self._report_recovery_error(gcmd, "G-Code modification time changed")
                return None

        root = os.path.realpath(self.virtual_sdcard.sdcard_dirname)
        real_file = os.path.realpath(gcode_file)
        try:
            inside_sdcard = os.path.commonpath([root, real_file]) == root
        except ValueError:
            inside_sdcard = False
        if not inside_sdcard:
            self._report_recovery_error(
                gcmd, "Failed to resurrect. G-Code file is outside virtual SD")
            return None

        if file_position:
            try:
                with open(gcode_file, "rb") as stream:
                    stream.seek(file_position - 1)
                    valid_boundary = stream.read(1) == b"\n"
            except (IOError, OSError) as e:
                self._report_recovery_error(
                    gcmd, f"Failed to resurrect. Unable to read G-Code: {e}")
                return None
            if not valid_boundary:
                self._report_recovery_error(
                    gcmd, "Failed to resurrect. File position is not a line boundary")
                return None

        state["_relative_path"] = os.path.relpath(real_file, root)
        self._checkpoint_cache = dict(state)
        self._checkpoint_cache.pop("_relative_path", None)
        self._checkpoint_cache_loaded = True
        return state

    def _load_state(self, stats):
        if "version" in stats:
            if type(stats["version"]) is not int or stats["version"] != snapshot.VERSION:
                raise RecoveryParseError("Unsupported checkpoint version")
            reference = stats.get("objects_ref")
            return snapshot.decode(stats, companion.read(self.file_path, reference))
        # The next regular post-resume save upgrades the old checkpoint.
        from .recovery.legacy import LegacyCheckpointLoader
        self._legacy_loader = LegacyCheckpointLoader(self.reactor)
        try:
            return self._legacy_loader.load(stats)
        finally:
            self._legacy_loader = None

    def _reset_recovery_context(self):
        try:
            self.gcode.run_script_from_command("_CONTEXT_RESET")
        except Exception:
            logging.exception("[resurrection] Failed to reset recovery context")

    def _rollback_recovery(self, gcmd, message, file_loaded=False):
        self.last_error = (self.last_error or "Failed to resurrect. %s" % message)
        logging.error("[resurrection] %s", self.last_error)
        if self.state != ResurrectorState.DESTROYED:
            self._change_state(ResurrectorState.RESURRECTION)
        if file_loaded:
            try:
                self.virtual_sdcard.do_cancel()
            except Exception:
                logging.exception(
                    "[resurrection] Failed to reset virtual SD after recovery")
        if self.state != ResurrectorState.DESTROYED:
            cleanup = ["TURN_OFF_HEATERS", "M106 P1 S0"]
            mod_params = getattr(self, "mod_params", None)
            if (file_loaded and mod_params is not None
                    and mod_params.variables.get("chamber_light_mode") == "PRINT_ONLY"):
                cleanup.append("LED_OFF")
            if file_loaded and self.printer.lookup_object("exclude_object", None) is not None:
                cleanup.append("EXCLUDE_OBJECT_DEFINE RESET=1")
            for command in cleanup:
                try:
                    self.gcode.run_script_from_command(command)
                except Exception:
                    logging.exception("[resurrection] Recovery cleanup failed: %s", command)
            self._reset_recovery_context()
        gcmd.respond_raw("!! " + self.last_error)

    def _restore_coordinate_state(self, state):
        self.gcode.run_script_from_command(
            "_SET_GCODE_OFFSET X=%s Y=%s Z=%s MOVE=0"
            % tuple(state.gcode_offsets))
        self.gcode_move.base_position[:3] = state.base_xyz

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

    def cmd_RESURRECT(self, gcmd):
        self.last_error = None
        if self.state != ResurrectorState.RESURRECTION:
            self._report_recovery_error(gcmd, "The printer isn’t in a resurrection state!")
            return

        logging.info("[resurrection] RESURRECT requested")

        file_loaded = False
        try:
            self.gcode.run_script_from_command("\n".join([
                '_CONTEXT_BEGIN TYPE=recovery',
                '_CONTEXT_STATE NAME="LOADING STATE"',
            ]))
            gcmd.respond_raw("// action:prompt_end")

            state = self._load_resurrection_state(gcmd)
            if state is None:
                self._reset_recovery_context()
                return

            mesh_name = state["mesh"]
            self._change_state(ResurrectorState.LOADING)
            parsed_state = self._load_state(state)
            if self.state == ResurrectorState.DESTROYED:
                return

            # Validate an actual mesh before heating or moving. Old checkpoints
            # contain only a profile name, so preserve the legacy fallback.
            mesh_status = self.bed_mesh.get_status(self.reactor.monotonic())
            saved_mesh = None
            if parsed_state.mesh_snapshot is not None:
                saved_mesh = companion.build_mesh(parsed_state.mesh_snapshot)
            elif mesh_name and mesh_name not in mesh_status["profiles"]:
                if 'auto' in mesh_status["profiles"]:
                    gcmd.respond_raw(f"!! Bed mesh missing: {mesh_name!r}. Using 'auto' instead...")
                    mesh_name = 'auto'
                else:
                    self._report_recovery_error(
                        gcmd, f"Failed to resurrect. Bed mesh missing: {mesh_name!r}")
                    self._change_state(ResurrectorState.RESURRECTION)
                    self._reset_recovery_context()
                    return

            skew = None
            if parsed_state.skew_factors is not None:
                skew = self.printer.lookup_object("skew_correction", None)
                if skew is None:
                    raise RecoveryParseError("Skew correction is unavailable")

            excluded = state.get("excluded_objects", [])
            exclusion = self.printer.lookup_object("exclude_object", None)
            if (excluded or parsed_state.exclusion_transform is not None
                    or parsed_state.objects) and exclusion is None:
                raise RecoveryParseError("Object exclusion is unavailable")
            object_commands = parsed_state.exclusion_commands(excluded) if exclusion is not None else []

            self.virtual_sdcard.load_file(gcmd, state["_relative_path"])
            # load_file resets the previous virtual-SD job. Claim the newly
            # loaded job only after that reset has completed successfully.
            self._recovery_active = True
            file_loaded = True
            self._change_state(ResurrectorState.PREPARING)

            toolhead_pos = [float(value) for value in state["position"][:4]]
            bed_temp = float(state["bed_temp"])
            extruder_temp = float(state["extruder_temp"])
            self.gcode.run_script_from_command("\n".join([
                "_CONTEXT_STATE NAME=PREPARING",
                "_START_PRINT_PREPARE SKIP_SKEW=1" if skew is not None
                else "_START_PRINT_PREPARE",
            ]))
            if skew is not None:
                skew._update_skew(*parsed_state.skew_factors)
            if parsed_state.mesh_snapshot is not None:
                companion.restore(self.bed_mesh, saved_mesh, mesh_name)
            elif mesh_name:
                self.gcode.run_script_from_command(f"BED_MESH_PROFILE LOAD={mesh_name}")
            else:
                self.gcode.run_script_from_command("BED_MESH_CLEAR")
            self.gcode.run_script_from_command("\n".join([
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
            ]))
            self._restore_coordinate_state(parsed_state)

            if skew is None:
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

            # Register the exclusion transform after F-only G1, which otherwise
            # could change the saved exclusion bookkeeping.
            if parsed_state.exclusion_transform is not None:
                if object_commands:
                    self.gcode.run_script_from_command("\n".join(object_commands))
                    object_commands = []
                snapshot.restore_exclusion(
                    exclusion, parsed_state, toolhead_pos[3],
                    self.toolhead.get_position()[3])
                self.gcode_move.reset_last_position()
                self.gcode.run_script_from_command(
                    "G92 E" + _format_number(parsed_state.logical_e))

            if parsed_state.has_retraction_state:
                firmware_retraction = self.printer.lookup_object(
                    "firmware_retraction", None)
                if firmware_retraction is None:
                    raise RecoveryParseError(
                        "Firmware retraction state can not be restored")
                firmware_retraction.is_retracted = parsed_state.retracted

            final_commands = parsed_state.final_commands()
            # Restoring layer metadata must not trigger pause/timelapse macros.
            if self.printer.lookup_object("gcode_macro SET_PRINT_STATS_INFO", None) is not None:
                final_commands = [
                    command.replace("SET_PRINT_STATS_INFO ", "SET_PRINT_STATS_INFO_BASE ", 1)
                    if command.startswith("SET_PRINT_STATS_INFO ") else command
                    for command in final_commands]
            final_commands.extend(object_commands)
            final_commands.extend([
                "SET_GCODE_VARIABLE MACRO=_START_PRINT "
                "VARIABLE=print_active VALUE=True",
                "SET_GCODE_VARIABLE MACRO=_START_PRINT "
                "VARIABLE=print_started VALUE=True",
            ])
            self.gcode.run_script_from_command("\n".join(final_commands))

            if excluded:
                if exclusion.next_transform is None:
                    raise RecoveryParseError("Object exclusion transform could not be restored")
                # Klipper normally primes a new exclusion transform with five
                # extrusion moves. Recovery has already restored the E state;
                # those moves must not print a currently excluded object.
                if parsed_state.exclusion_transform is None:
                    exclusion.initial_extrusion_moves = 0

            # do_resume schedules SD work; it does not execute the next line.
            # Publish the result before ending the recovery context.
            self.virtual_sdcard.do_resume()
            self._change_state(ResurrectorState.PRINTING)
        except Exception as error:
            if self.state != ResurrectorState.DESTROYED:
                self._rollback_recovery(
                    gcmd, str(error) or error.__class__.__name__, file_loaded)
            return

        # SD has been scheduled successfully. A context or notification
        # failure must not cancel the restored print or turn off its heaters.
        try:
            self.gcode.run_script_from_command("\n".join([
                "_CONTEXT_END",
                "_CONTEXT_BEGIN TYPE=print",
                "_CONTEXT_STATE NAME=PRINTING",
            ]))
            self.gcode.respond_raw("// Resurrection finished!")
        except Exception:
            logging.exception("[resurrection] Failed to publish recovery completion")
            self._reset_recovery_context()

    def cmd_RESURRECT_ABORT(self, gcmd):
        self.last_error = None
        if self.state != ResurrectorState.RESURRECTION:
            self._report_recovery_error(gcmd, "The printer isn’t in a resurrection state!")
            return

        try:
            self.gcode.run_script_from_command("\n".join([
                '_CONTEXT_BEGIN TYPE=recovery',
                '_CONTEXT_STATE NAME="LOADING STATE"',
            ]))
            gcmd.respond_raw("// action:prompt_end")
            state = self._load_resurrection_state(gcmd, cleanup=True)
            if state is None:
                self._reset_recovery_context()
                return

            self.gcode.run_script_from_command("\n".join([
                '_CONTEXT_STATE NAME=PREPARING',
                f"_WAIT_TEMPERATURE CMD=M140 VALUE={state['bed_temp']} BELOW=2 ABOVE=3",
                f"_WAIT_TEMPERATURE CMD=M104 VALUE={state['extruder_temp']}",

                "_HOME_IF_NEEDED",
                f"M400",

                "_CONTEXT_STATE NAME=FINISHING",
                "TURN_OFF_HEATERS",
            ]))
            error = self._clear(self.reactor.monotonic())
            if error is not None:
                raise RecoveryParseError(
                    "Failed to remove resurrection file: %s" % error)
        except Exception as error:
            if self.state != ResurrectorState.DESTROYED:
                self._rollback_recovery(
                    gcmd, str(error) or error.__class__.__name__)
            return

        # Removing the checkpoint commits cleanup. A display/context failure
        # afterwards cannot undo the deletion.
        self._change_state(ResurrectorState.IDLE)
        try:
            self.gcode.run_script_from_command("_CONTEXT_END")
        except Exception:
            logging.exception("[resurrection] Failed to end cleanup context")
            self._reset_recovery_context()
        self.gcode.respond_raw("// Resurrection aborted!")


def load_config(config):
    return Resurrector(config)
