## Observable object-exclusion checkpoint and recovery contracts.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import json
import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from tests.test_gcode_patch import GCODE, RecordingMutex
from tests.test_resurrection import (
    RESURRECTION, Command, CooperativeReactor, VirtualSDRecorder,
    move_status, toolhead_status, extruder_status, inactive_bed_mesh, active_mesh, MeshModel,
)


class ExclusionProtocol:
    """Consume real parsed commands and expose Klipper's object status contract."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.objects = []
        self.excluded_objects = []
        self.current_object = None
        self.next_transform = None
        self.printed_objects = []
        self.last_position = [15., 25., .3, 5.]
        self.last_position_extruded = list(self.last_position)
        self.last_position_excluded = list(self.last_position)
        self.max_position_extruded = self.max_position_excluded = 5.
        self.extruder_adj = 0.
        self.extrusion_offsets = {"extruder": [0., 0., 0., 0.]}
        self.in_excluded_region = False
        self.initial_extrusion_moves = 0

    def get_status(self, eventtime):
        return dict(objects=self.objects, excluded_objects=self.excluded_objects,
                    current_object=self.current_object)

    def define(self, gcmd):
        if gcmd.get('RESET', None):
            self.reset()
            return
        definition = dict(name=gcmd.get('NAME').upper())
        center = gcmd.get('CENTER', None)
        polygon = gcmd.get('POLYGON', None)
        if center is not None:
            definition['center'] = json.loads('[' + center + ']')
        if polygon is not None:
            definition['polygon'] = json.loads(polygon)
        self.objects = sorted(self.objects + [definition], key=lambda item: item["name"])

    def exclude(self, gcmd):
        if set(gcmd.get_command_parameters()) != {'NAME'}:
            raise gcmd.error('Unexpected exclusion parameters')
        name = gcmd.get('NAME').upper()
        if name not in self.excluded_objects:
            self.excluded_objects.append(name)
        self.next_transform = self
        self.initial_extrusion_moves = 5

    def start(self, gcmd):
        self.current_object = gcmd.get('NAME').upper()

    def extrude(self):
        # Model Klipper's initial transform priming in the protocol consumer.
        if self.next_transform is not None:
            if self.initial_extrusion_moves == 0 and self.current_object in self.excluded_objects:
                return
            if self.initial_extrusion_moves:
                self.initial_extrusion_moves -= 1
        self.printed_objects.append(self.current_object)


class ResurrectionExclusionsTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = pathlib.Path(directory.name)
        self.file = self.root / 'models.gcode'
        self.file.write_text(
            'G90\nM82\n'
            'EXCLUDE_OBJECT_DEFINE NAME="КРЫШКА ЛЕВАЯ" CENTER=15,25 POLYGON=[[10,20],[20,20],[20,30]]\n'
            'EXCLUDE_OBJECT_DEFINE NAME=PART_B\n'
            'EXCLUDE_OBJECT_START NAME="КРЫШКА ЛЕВАЯ"\n'
            'G1 X15 Y25 E5 F900\n', encoding='utf-8')
        self.exclusion = ExclusionProtocol()
        self.exclusion.objects = [
            {"name": "PART_B"},
            {"name": "КРЫШКА ЛЕВАЯ", "center": [15, 25],
             "polygon": [[10, 20], [20, 20], [20, 30]]}]
        self.exclusion.excluded_objects = ['КРЫШКА ЛЕВАЯ']
        self.exclusion.current_object = 'КРЫШКА ЛЕВАЯ'
        self.stats = dict(state='printing')
        self.position = [15., 25., .3, 5.]
        self.sd_status = dict(file_path=str(self.file), file_size=self.file.stat().st_size,
                              file_position=self.file.stat().st_size,
                              file_mtime_ns=self.file.stat().st_mtime_ns)
        self.commands = []
        self.effects = []
        dispatch = GCODE.GCodeDispatch.__new__(GCODE.GCodeDispatch)
        dispatch.printer = SimpleNamespace(config_error=RuntimeError, send_event=lambda *args: None)
        dispatch.mutex = RecordingMutex()
        dispatch.output_callbacks = []
        dispatch.is_fileinput = False
        dispatch.base_gcode_handlers = {}
        dispatch.gcode_handlers = dispatch.ready_gcode_handlers = {}
        dispatch.gcode_help = {}
        dispatch.cmd_default = lambda gcmd: None
        dispatch.register_command('EXCLUDE_OBJECT_DEFINE', self.exclusion.define)
        dispatch.register_command('EXCLUDE_OBJECT', self.exclusion.exclude)
        dispatch.register_command('EXCLUDE_OBJECT_START', self.exclusion.start)

        def run(script):
            lines = script.splitlines()
            self.commands.extend(lines)
            self.effects.append('context' if all(
                line.startswith('_CONTEXT_') for line in lines) else 'gcode')
            dispatch.run_script_from_command(script)

        recovery = self.recovery = RESURRECTION.Resurrector.__new__(RESURRECTION.Resurrector)
        recovery.printer = SimpleNamespace(lookup_object=lambda name, default=None:
                                            self.exclusion if name == 'exclude_object' else default)
        recovery.gcode = SimpleNamespace(
            run_script_from_command=run, respond_raw=lambda message: None,
            get_mutex=lambda: SimpleNamespace(test=lambda: False))
        recovery.reactor = CooperativeReactor()
        recovery.file_path = str(self.root / 'checkpoint.json')
        recovery.state = RESURRECTION.ResurrectorState.PRINTING
        recovery.enabled = True
        recovery.dump_time = 3.
        recovery._recovery_active = False
        recovery._legacy_loader = None
        recovery._checkpoint_cache = None
        recovery._checkpoint_cache_loaded = False
        recovery.print_stats = SimpleNamespace(get_status=lambda eventtime: self.stats)
        recovery.start_print_macro = SimpleNamespace(variables=dict(print_started=True))
        def reset_sd():
            self.exclusion.reset()
            recovery._handle_virtual_sd_reset()

        recovery.virtual_sdcard = VirtualSDRecorder(on_load=reset_sd,
                                                     on_resume=lambda: self.effects.append('resume'))
        recovery.virtual_sdcard.sdcard_dirname = str(self.root)
        recovery.virtual_sdcard.get_status = lambda eventtime: self.sd_status
        recovery.virtual_sdcard.is_cmd_from_sd = lambda: False
        recovery.virtual_sdcard.is_active = lambda: self.stats['state'] == 'printing'
        recovery.toolhead = SimpleNamespace(
            get_status=lambda eventtime: toolhead_status(position=self.position),
            get_position=lambda: self.position)
        recovery.extruder = SimpleNamespace(get_status=lambda eventtime: extruder_status())
        recovery.heater_bed = SimpleNamespace(get_status=lambda eventtime: dict(target=60.))
        recovery.bed_mesh = inactive_bed_mesh("auto")
        recovery.gcode_move = SimpleNamespace(
            get_status=lambda eventtime: move_status(gcode_position=self.position),
            base_position=[0., 0., 0., 0.], homing_position=[0., 0., 0., 0.],
            reset_last_position=lambda: None)
        recovery._restore_physical_position = lambda position: self.effects.append('position')

    def checkpoint(self):
        return json.loads(pathlib.Path(self.recovery.file_path).read_text(encoding='utf-8'))

    def recover(self):
        self.recovery._change_state(RESURRECTION.ResurrectorState.RESURRECTION)
        command = Command()
        self.recovery.cmd_RESURRECT(command)
        return command

    def test_checkpoint_stores_only_stable_names_and_omits_empty_exclusions(self):
        self.exclusion.excluded_objects = ['PART_B', 'КРЫШКА ЛЕВАЯ', 'PART_B']
        self.recovery._dump(0.)
        checkpoint = self.checkpoint()
        self.assertEqual(checkpoint['excluded_objects'], ['PART_B', 'КРЫШКА ЛЕВАЯ'])
        self.assertNotIn('objects', checkpoint)
        self.assertEqual(checkpoint['current_object'], 'КРЫШКА ЛЕВАЯ')
        self.assertNotIn('gcode_state', checkpoint)
        static = RESURRECTION.companion.read(self.recovery.file_path, checkpoint['objects_ref'])
        self.assertEqual(len(static['objects']), 2)
        # ASCII JSON stays readable by older loaders even under a C locale.
        pathlib.Path(self.recovery.file_path).read_bytes().decode('ascii')
        self.assertFalse(pathlib.Path(self.recovery.file_path + '.tmp').exists())
        self.exclusion.excluded_objects.clear()
        self.recovery._dump(0.)
        self.assertNotIn('excluded_objects', self.checkpoint())
        self.assertEqual(self.checkpoint()['objects_ref'], checkpoint['objects_ref'])

    def test_missing_companion_rejects_recovery_before_loading_gcode(self):
        self.recovery._dump(0.)
        checkpoint = self.checkpoint()
        pathlib.Path(self.recovery.file_path).with_name(checkpoint["objects_ref"]).unlink()
        command = self.recover()
        self.assertTrue(any("recovery objects" in line for line in command.responses))
        self.assertFalse(self.recovery.virtual_sdcard.loaded)
        self.assertFalse(self.recovery.virtual_sdcard.resumed)

    def test_unsaved_mesh_restored_without_loading_stale_profile(self):
        mesh = active_mesh()
        bed = self.recovery.bed_mesh = SimpleNamespace(
            get_status=lambda eventtime: {"profile_name": "temporary", "profiles": ["old"]},
            mesh=mesh, pmgr=SimpleNamespace(current_profile="old"))
        bed.get_mesh = lambda: bed.mesh
        bed.set_mesh = lambda restored: setattr(bed, "mesh", restored)
        skew = SimpleNamespace(xy_factor=.01, xz_factor=0., yz_factor=.02,
                               _update_skew=mock.Mock())
        self.recovery.printer.lookup_object = lambda name, default=None: {
            "exclude_object": self.exclusion, "skew_correction": skew}.get(name, default)
        self.recovery._dump(0.)
        reference = self.checkpoint()["objects_ref"]
        original = self.root / reference
        before = original.stat()
        original_data = original.read_bytes()
        old_build = RESURRECTION.companion.build_mesh
        with mock.patch.object(RESURRECTION.companion, "build_mesh",
                               side_effect=lambda data: old_build(data, factory=MeshModel)):
            command = self.recover()
        self.assertFalse(any(line.startswith("!!") for line in command.responses))
        self.assertTrue(self.recovery.virtual_sdcard.resumed)
        restored = bed.mesh
        self.assertEqual(restored.probed_matrix, mesh.probed_matrix)
        self.assertEqual(restored.mesh_offsets, mesh.mesh_offsets)
        self.assertEqual(restored.get_mesh_params(), mesh.get_mesh_params())
        self.assertEqual(self.recovery.bed_mesh.pmgr.current_profile, "temporary")
        self.assertIn("_START_PRINT_PREPARE SKIP_SKEW=1", self.commands)
        skew._update_skew.assert_called_once_with(.01, 0., .02)
        self.assertFalse(any(line.startswith("BED_MESH_PROFILE LOAD") for line in self.commands))

        # A completed resumed command advances the main checkpoint, not its companion.
        self.position[0] = 23.
        self.recovery._handle_command_boundary(1.)
        self.assertEqual(self.checkpoint()["position"][0], 23.)
        self.assertEqual(self.checkpoint()["objects_ref"], reference)
        self.assertEqual(original.read_bytes(), original_data)
        self.assertEqual(original.stat().st_ino, before.st_ino)
        self.assertEqual(original.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(list(self.root.glob("checkpoint.objects.*.json")), [original])

        # An actual change to the mesh invalidates the preserved reference.
        restored.set_mesh_offsets([1., -.5])
        self.recovery._handle_command_boundary(2.)
        updated = self.checkpoint()["objects_ref"]
        self.assertNotEqual(updated, reference)
        self.assertFalse(original.exists())
        self.assertEqual(json.loads((self.root / updated).read_text())["mesh"]["offsets"],
                         [1., -.5])

    def test_changed_definitions_before_first_resumed_checkpoint_replace_companion(self):
        self.recovery._dump(0.)
        reference = self.checkpoint()["objects_ref"]
        original = self.root / reference
        checkpoint_path = pathlib.Path(self.recovery.file_path)
        before = checkpoint_path.read_bytes()
        self.recover()
        self.assertTrue(self.recovery.virtual_sdcard.resumed)
        self.recovery.gcode.run_script_from_command("EXCLUDE_OBJECT_DEFINE NAME=PART_C")
        real_replace = RESURRECTION.os.replace

        def fail_checkpoint(source, target):
            if pathlib.Path(target) == checkpoint_path:
                raise OSError("checkpoint write failed")
            real_replace(source, target)

        with mock.patch.object(RESURRECTION.os, "replace", side_effect=fail_checkpoint):
            self.recovery._handle_command_boundary(1.)
        self.assertEqual(checkpoint_path.read_bytes(), before)
        self.assertTrue(original.is_file())
        self.recovery._handle_command_boundary(2.)
        updated = self.checkpoint()["objects_ref"]
        self.assertNotEqual(updated, reference)
        self.assertFalse(original.exists())
        data = json.loads((self.root / updated).read_text())
        self.assertEqual({item["name"] for item in data["objects"]},
                         {"PART_B", "КРЫШКА ЛЕВАЯ", "PART_C"})
        self.assertEqual(list(self.root.glob("checkpoint.objects.*.json")),
                         [self.root / updated])

    def test_recovery_restores_geometry_exclusions_and_current_marker_before_resume(self):
        self.recovery._dump(0.)
        command = self.recover()
        self.assertFalse(any(response.startswith('!!') for response in command.responses))
        self.assertTrue(self.recovery.virtual_sdcard.resumed)
        self.assertEqual(self.exclusion.excluded_objects, ['КРЫШКА ЛЕВАЯ'])
        self.assertEqual(self.exclusion.current_object, 'КРЫШКА ЛЕВАЯ')
        self.assertEqual(
            {item['name']: item for item in self.exclusion.objects},
            {
                'КРЫШКА ЛЕВАЯ': {'name': 'КРЫШКА ЛЕВАЯ', 'center': [15, 25],
                                  'polygon': [[10, 20], [20, 20], [20, 30]]},
                'PART_B': {'name': 'PART_B'},
            })
        self.assertGreater(self.commands.index("EXCLUDE_OBJECT NAME='КРЫШКА ЛЕВАЯ'"),
                           self.commands.index('G92 E5'))
        # All print-state commands must precede resume; only context
        # bookkeeping can follow the scheduling of virtual SD work.
        self.assertEqual(self.effects[-2:], ['resume', 'context'])

    def test_exclusions_added_after_recovery_survive_another_checkpoint_and_recovery(self):
        self.recovery._dump(0.)
        reference = self.checkpoint()["objects_ref"]
        self.recover()
        self.recovery.gcode.run_script_from_command("EXCLUDE_OBJECT NAME=PART_B")
        self.recovery._handle_command_boundary(1.)
        self.assertEqual(self.checkpoint()["objects_ref"], reference)
        self.recovery.virtual_sdcard.resumed = False
        self.recover()
        self.assertEqual(set(self.exclusion.excluded_objects), {'КРЫШКА ЛЕВАЯ', 'PART_B'})
        self.assertTrue(self.recovery.virtual_sdcard.resumed)
        self.recovery._handle_command_boundary(2.)
        self.assertEqual(self.checkpoint()["objects_ref"], reference)

    def test_first_resumed_moves_inside_excluded_object_are_skipped(self):
        self.recovery._dump(0.)

        def resume():
            for _ in range(6):
                self.exclusion.extrude()
            self.exclusion.current_object = 'PART_B'
            self.exclusion.extrude()

        self.recovery.virtual_sdcard.on_resume = resume
        self.recover()
        self.assertEqual(self.exclusion.printed_objects, ['PART_B'])

    def test_parameter_like_names_and_quotes_remain_identifiers(self):
        names = ['PART_B RESET=1', 'ПРАВАЯ "КРЫШКА"', "O'BRIEN"]
        self.exclusion.excluded_objects = list(names)
        self.recovery._dump(0.)
        self.recover()
        self.assertEqual(set(self.exclusion.excluded_objects), set(names))
        self.assertEqual({item['name'] for item in self.exclusion.objects}, {'КРЫШКА ЛЕВАЯ', 'PART_B'})

    def test_checkpoint_without_new_field_and_with_unknown_fields_needs_no_migration(self):
        self.exclusion.excluded_objects.clear()
        self.recovery._dump(0.)
        checkpoint = self.checkpoint()
        self.assertNotIn('excluded_objects', checkpoint)
        checkpoint['future_extension'] = dict(version=9)
        pathlib.Path(self.recovery.file_path).write_text(json.dumps(checkpoint), encoding='utf-8')
        self.recover()
        self.assertTrue(self.recovery.virtual_sdcard.resumed)
        self.assertEqual(self.exclusion.excluded_objects, [])

    def test_paused_exclusion_updates_do_not_overwrite_pre_park_position(self):
        self.exclusion.excluded_objects.clear()
        self.recovery.cmd_RESURRECTION_PAUSE(Command())
        frozen = self.checkpoint()
        self.position = [0., 0., 25., 5.]
        self.stats['state'] = 'paused'
        self.exclusion.excluded_objects.append('PART_B')
        self.recovery._dump_timer_handler(1.)
        updated = self.checkpoint()
        self.assertEqual(updated.pop('excluded_objects'), ['PART_B'])
        self.assertEqual(updated, frozen)
        with mock.patch.object(self.recovery, '_write_checkpoint') as write:
            self.recovery._dump_timer_handler(2.)
        write.assert_not_called()

    def test_paused_exclusion_reset_removes_field_and_different_job_is_not_updated(self):
        self.recovery.cmd_RESURRECTION_PAUSE(Command())
        self.exclusion.excluded_objects.clear()
        self.stats['state'] = 'paused'
        self.recovery._dump_timer_handler(1.)
        self.assertNotIn('excluded_objects', self.checkpoint())
        saved = pathlib.Path(self.recovery.file_path).read_bytes()
        self.sd_status['file_path'] = '/different.gcode'
        self.exclusion.excluded_objects.append('PART_B')
        self.recovery._dump_timer_handler(2.)
        self.assertEqual(pathlib.Path(self.recovery.file_path).read_bytes(), saved)

    def test_invalid_exclusion_data_is_rejected_before_loading_or_preparation(self):
        self.recovery._dump(0.)
        valid = self.checkpoint()
        for invalid in (None, 'PART_B', {}, [42], [''], ['PART_B\nM112'], ['PART#B'], ['PART;B']):
            with self.subTest(excluded_objects=invalid):
                checkpoint = dict(valid, excluded_objects=invalid)
                pathlib.Path(self.recovery.file_path).write_text(json.dumps(checkpoint), encoding='utf-8')
                self.commands.clear()
                command = self.recover()
                self.assertFalse(self.recovery.virtual_sdcard.resumed)
                self.assertEqual(self.recovery.virtual_sdcard.loaded, [])
                self.assertNotIn('_START_PRINT_PREPARE', self.commands)
                self.assertTrue(any('excluded' in response.lower() for response in command.responses))

    def test_missing_exclusion_plugin_blocks_recovery_instead_of_printing_excluded_models(self):
        self.recovery._dump(0.)
        self.recovery.printer.lookup_object = lambda name, default=None: default
        with self.assertLogs(level='ERROR'):
            command = self.recover()
        self.assertFalse(self.recovery.virtual_sdcard.resumed)
        self.assertEqual(self.recovery.virtual_sdcard.loaded, [])
        self.assertTrue(any('exclusion' in response.lower() for response in command.responses))

    def test_exclusion_command_failure_cancels_loaded_job_and_keeps_checkpoint(self):
        self.recovery._dump(0.)
        saved = pathlib.Path(self.recovery.file_path).read_bytes()
        run = self.recovery.gcode.run_script_from_command

        def fail(script):
            for line in script.splitlines():
                if line.startswith('EXCLUDE_OBJECT_START '):
                    raise RuntimeError('exclusion failed')
                run(line)

        self.recovery.gcode.run_script_from_command = fail
        with self.assertLogs(level='ERROR'):
            command = self.recover()
        self.assertTrue(self.recovery.virtual_sdcard.cancelled)
        self.assertFalse(self.recovery.virtual_sdcard.resumed)
        self.assertEqual(pathlib.Path(self.recovery.file_path).read_bytes(), saved)
        self.assertIn('TURN_OFF_HEATERS', self.commands)
        self.assertTrue(any('exclusion failed' in response for response in command.responses))
        self.assertEqual(self.exclusion.excluded_objects, [])
        self.assertIsNone(self.exclusion.current_object)
        self.recovery.gcode.run_script_from_command = run
        self.recover()
        self.assertTrue(self.recovery.virtual_sdcard.resumed)
        self.assertEqual(self.exclusion.excluded_objects, ['КРЫШКА ЛЕВАЯ'])

    def test_rollback_attempts_remaining_cleanup_after_secondary_failure(self):
        self.recovery._dump(0.)
        before = pathlib.Path(self.recovery.file_path).read_bytes()
        run = self.recovery.gcode.run_script_from_command

        def fail(script):
            if '_START_PRINT_PREPARE' in script:
                raise RuntimeError('preparation failed')
            if script == 'TURN_OFF_HEATERS':
                raise RuntimeError('heater cleanup failed')
            run(script)

        self.recovery.gcode.run_script_from_command = fail
        with self.assertLogs(level='ERROR'):
            command = self.recover()
        self.assertIn('preparation failed', self.recovery.last_error)
        self.assertIn('preparation failed', command.responses[-1])
        self.assertIn('M106 P1 S0', self.commands)
        self.assertIn('_CONTEXT_RESET', self.commands)
        self.assertTrue(self.recovery.virtual_sdcard.cancelled)
        self.assertEqual(pathlib.Path(self.recovery.file_path).read_bytes(), before)

    def test_mesh_status_failure_is_handled_as_recovery_failure(self):
        self.recovery._dump(0.)
        before = pathlib.Path(self.recovery.file_path).read_bytes()
        self.recovery.bed_mesh.get_status = mock.Mock(
            side_effect=RuntimeError('mesh status failed'))
        with self.assertLogs(level='ERROR'):
            command = self.recover()
        self.assertIn('mesh status failed', command.responses[-1])
        self.assertIn('_CONTEXT_RESET', self.commands)
        self.assertFalse(self.recovery.virtual_sdcard.loaded)
        self.assertFalse(self.recovery.virtual_sdcard.resumed)
        self.assertEqual(pathlib.Path(self.recovery.file_path).read_bytes(), before)

    def test_legacy_recovery_upgrades_only_at_next_regular_checkpoint(self):
        self.recovery._dump(0.)
        old = self.checkpoint()
        old = {key: old[key] for key in (
            "file_path", "file_position", "file_size", "position",
            "extruder_temp", "bed_temp", "mesh", "excluded_objects")}
        old["z_offset"] = 0.
        original = json.dumps(old).encode()
        pathlib.Path(self.recovery.file_path).write_bytes(original)
        self.recover()
        self.assertTrue(self.recovery.virtual_sdcard.resumed)
        self.assertEqual(pathlib.Path(self.recovery.file_path).read_bytes(), original)
        self.recovery._dump(1.)
        self.assertEqual(self.checkpoint()["version"], 2)
        self.assertNotIn("source", self.checkpoint())

    def test_markerless_pause_keeps_checkpoint_until_completed_sd_line(self):
        self.recovery._dump(0.)
        previous = pathlib.Path(self.recovery.file_path).read_bytes()
        pause = SimpleNamespace(is_paused=True)
        lookup = self.recovery.printer.lookup_object
        self.recovery.printer.lookup_object = lambda name, default=None: (
            pause if name == "pause_resume" else lookup(name, default))
        # During an SD command's pause, print_stats can still say printing.
        self.position[0] = 99.
        self.recovery._dump_timer_handler(3.)
        self.recovery._handle_command_boundary(3.)
        self.recovery.virtual_sdcard.request_command_boundary.assert_not_called()
        self.assertEqual(pathlib.Path(self.recovery.file_path).read_bytes(), previous)
        # Once the SD worker stops, the generic pause is detected without
        # knowing which macro parked the head.
        self.stats["state"] = "paused"
        self.recovery._dump_timer_handler(6.)
        self.assertEqual(self.recovery.state, RESURRECTION.ResurrectorState.PAUSED)
        self.assertEqual(pathlib.Path(self.recovery.file_path).read_bytes(), previous)
        # Automatic resume needs no marker, either.
        pause.is_paused = False
        self.stats["state"] = "printing"
        self.position[0] = 23.
        self.recovery._dump_timer_handler(9.)
        self.recovery.virtual_sdcard.request_command_boundary.assert_called_once_with()
        self.assertEqual(pathlib.Path(self.recovery.file_path).read_bytes(), previous)
        self.recovery._handle_command_boundary(9.)
        self.assertEqual(self.checkpoint()["position"][0], 23.)

    def test_context_failure_after_resume_does_not_cancel_restored_print(self):
        self.recovery._dump(0.)
        before = pathlib.Path(self.recovery.file_path).read_bytes()
        run = self.recovery.gcode.run_script_from_command

        def fail_context(script):
            if script.startswith("_CONTEXT_END\n"):
                raise RuntimeError("context failed after SD resume")
            run(script)

        self.recovery.gcode.run_script_from_command = fail_context
        command = self.recover()
        self.assertTrue(self.recovery.virtual_sdcard.resumed)
        self.assertFalse(self.recovery.virtual_sdcard.cancelled)
        self.assertEqual(self.recovery.state, RESURRECTION.ResurrectorState.PRINTING)
        self.assertIsNone(self.recovery.last_error)
        self.assertFalse(any(line.startswith("!!") for line in command.responses))
        self.assertIn("_CONTEXT_RESET", self.commands)
        self.assertEqual(pathlib.Path(self.recovery.file_path).read_bytes(), before)

    def test_failed_write_preserves_print_and_next_tick_saves(self):
        self.recovery._dump(0.)
        previous = pathlib.Path(self.recovery.file_path).read_bytes()
        self.position[0] = 23.
        with mock.patch.object(RESURRECTION.os, "replace",
                               side_effect=OSError("temporary write failure")):
            self.recovery._dump(1.)
        self.assertEqual(pathlib.Path(self.recovery.file_path).read_bytes(), previous)
        self.assertEqual(self.recovery.state, RESURRECTION.ResurrectorState.PRINTING)
        self.recovery._dump_timer_handler(3.)
        self.recovery._handle_command_boundary(3.)
        self.assertEqual(self.checkpoint()["position"][0], 23.)

    def test_failed_sd_line_keeps_last_completed_checkpoint(self):
        self.recovery._dump(0.)
        previous = pathlib.Path(self.recovery.file_path).read_bytes()
        self.position[0] = 99.
        self.stats["state"] = "error"
        self.recovery._dump_timer_handler(3.)
        self.assertEqual(pathlib.Path(self.recovery.file_path).read_bytes(), previous)

    def test_dump_uses_loaded_file_metadata_without_restat(self):
        self.recovery._dump(0.)
        original_stat = RESURRECTION.os.stat

        def stat(path, *args, **kwargs):
            if str(path) == str(self.file):
                raise AssertionError("checkpoint must use loaded file metadata")
            return original_stat(path, *args, **kwargs)

        with mock.patch.object(RESURRECTION.os, "stat", side_effect=stat):
            self.position[0] = 21.
            self.recovery._dump(3.)
        self.assertEqual(self.checkpoint()["position"][0], 21.)

    def test_atomic_write_failure_keeps_previous_checkpoint_and_cache(self):
        self.recovery._dump(0.)
        saved = pathlib.Path(self.recovery.file_path).read_bytes()
        cached = self.recovery._checkpoint_cache
        self.exclusion.excluded_objects.append('PART_B')
        with mock.patch.object(RESURRECTION.os, 'replace', side_effect=OSError('write failed')):
            with self.assertLogs(level='ERROR'):
                self.recovery._dump(1.)
        self.assertEqual(pathlib.Path(self.recovery.file_path).read_bytes(), saved)
        self.assertEqual(self.recovery._checkpoint_cache, cached)
        self.assertFalse(pathlib.Path(self.recovery.file_path + '.tmp').exists())


if __name__ == '__main__':
    unittest.main()
