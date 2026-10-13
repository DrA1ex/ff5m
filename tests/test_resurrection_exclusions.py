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
        self.objects.append(definition)

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
        self.exclusion.excluded_objects = ['КРЫШКА ЛЕВАЯ']
        self.exclusion.current_object = 'КРЫШКА ЛЕВАЯ'
        self.stats = dict(state='printing')
        self.position = [15., 25., .3, 5.]
        self.sd_status = dict(file_path=str(self.file), file_size=self.file.stat().st_size,
                              file_position=self.file.stat().st_size)
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
            self.commands.extend(script.splitlines())
            self.effects.append('gcode')
            dispatch.run_script_from_command(script)

        recovery = self.recovery = RESURRECTION.Resurrector.__new__(RESURRECTION.Resurrector)
        recovery.printer = SimpleNamespace(lookup_object=lambda name, default=None:
                                            self.exclusion if name == 'exclude_object' else default)
        recovery.gcode = SimpleNamespace(run_script_from_command=run, respond_raw=lambda message: None)
        recovery.reactor = CooperativeReactor()
        recovery.file_path = str(self.root / 'checkpoint.json')
        recovery.state = RESURRECTION.ResurrectorState.PRINTING
        recovery.enabled = True
        recovery.dump_time = 3.
        recovery._pause_checkpoint_active = False
        recovery._resume_pending = False
        recovery._recovery_active = False
        recovery._worker = recovery._worker_cancel = None
        recovery._checkpoint_cache = None
        recovery._checkpoint_cache_loaded = False
        recovery.print_stats = SimpleNamespace(get_status=lambda eventtime: self.stats)
        recovery.start_print_macro = SimpleNamespace(variables=dict(print_started=True))
        recovery.virtual_sdcard = VirtualSDRecorder(on_load=self.exclusion.reset,
                                                     on_resume=lambda: self.effects.append('resume'))
        recovery.virtual_sdcard.sdcard_dirname = str(self.root)
        recovery.virtual_sdcard.get_status = lambda eventtime: self.sd_status
        recovery.virtual_sdcard.is_cmd_from_sd = lambda: False
        recovery.toolhead = SimpleNamespace(get_status=lambda eventtime: dict(position=self.position))
        recovery.extruder = SimpleNamespace(get_status=lambda eventtime: dict(target=220.))
        recovery.heater_bed = SimpleNamespace(get_status=lambda eventtime: dict(target=60.))
        recovery.bed_mesh = SimpleNamespace(get_status=lambda eventtime: dict(profile_name='auto', profiles=['auto']))
        recovery.gcode_move = SimpleNamespace(get_status=lambda eventtime: dict(homing_origin=[0., 0., 0.]))
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
        self.assertNotIn('current_object', checkpoint)
        # ASCII JSON stays readable by older loaders even under a C locale.
        pathlib.Path(self.recovery.file_path).read_bytes().decode('ascii')
        self.assertFalse(pathlib.Path(self.recovery.file_path + '.tmp').exists())
        self.exclusion.excluded_objects.clear()
        self.recovery._dump(0.)
        self.assertNotIn('excluded_objects', self.checkpoint())

    def test_recovery_restores_geometry_exclusions_and_current_marker_before_resume(self):
        self.recovery._dump(0.)
        command = self.recover()
        self.assertFalse(any(response.startswith('!!') for response in command.responses))
        self.assertTrue(self.recovery.virtual_sdcard.resumed)
        self.assertEqual(self.exclusion.excluded_objects, ['КРЫШКА ЛЕВАЯ'])
        self.assertEqual(self.exclusion.current_object, 'КРЫШКА ЛЕВАЯ')
        self.assertEqual(self.exclusion.objects[0]['polygon'], [[10, 20], [20, 20], [20, 30]])
        self.assertEqual({item['name'] for item in self.exclusion.objects}, {'КРЫШКА ЛЕВАЯ', 'PART_B'})
        self.assertGreater(self.commands.index("EXCLUDE_OBJECT NAME='КРЫШКА ЛЕВАЯ'"),
                           self.commands.index('G92 E5'))
        self.assertEqual(self.effects[-1], 'resume')

    def test_exclusions_added_after_recovery_survive_another_checkpoint_and_recovery(self):
        self.recovery._dump(0.)
        self.recover()
        self.exclusion.excluded_objects.append('PART_B')
        self.recovery._dump(1.)
        self.recovery.virtual_sdcard.resumed = False
        self.recover()
        self.assertEqual(set(self.exclusion.excluded_objects), {'КРЫШКА ЛЕВАЯ', 'PART_B'})
        self.assertTrue(self.recovery.virtual_sdcard.resumed)

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
