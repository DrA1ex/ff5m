## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

"""Exercise the validation templates with Klipper's shipped G-code state code.

Run with Python 3.12+ and Jinja2:
    python -m unittest discover -s tests -v
This harness verifies command/state behavior; it does not simulate mechanics.
"""
import ast
import configparser
import importlib.util
import shlex
import tempfile
import unittest
from collections import namedtuple
from pathlib import Path
from types import SimpleNamespace

import jinja2

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ModParams = load_module('mod_params', '.py/klipper/plugins/mod_params.py')
GCodeMove = load_module('gcode_move', '.py/klipper/patches/extras/gcode_move.py')
from tests.gcode_macro_harness import load_macro
Coord = namedtuple('Coord', 'x y z e')


class Command:
    def __init__(self, params):
        self.params = params

    def get(self, key, default=None):
        return self.params.get(key, default)

    def get_float(self, key, default=None, **kwargs):
        value = self.get(key, default)
        return float(value) if value is not None else None

    def get_int(self, key, default=None, **kwargs):
        value = self.get(key, default)
        return int(value) if value is not None else None

    def get_command_parameters(self):
        return self.params

    def error(self, message):
        return RuntimeError(message)

    def respond_raw(self, message):
        pass

    def respond_info(self, message):
        pass


class Cancelled(RuntimeError):
    pass


class Harness:
    def __init__(self, directory, action=0, offset=0.05, shifts=None, matrix=None, offset_mode=0):
        self.trace = []
        self.macros = {}
        self.variables = {}
        self.env = jinja2.Environment(
            block_start_string='{%', block_end_string='%}',
            variable_start_string='{', variable_end_string='}',
            undefined=jinja2.StrictUndefined,
        )
        config = configparser.RawConfigParser()
        config.read(ROOT / 'macros/base.cfg')
        for section in config.sections():
            if not section.startswith('gcode_macro '):
                continue
            name = section[len('gcode_macro '):]
            source = load_macro(ROOT / 'macros/base.cfg', name)
            self.macros[name] = self.env.from_string(source.gcode)
            self.variables['gcode_macro ' + name] = dict(source.variables)

        self.mod = ModParams.ModParamManagement.__new__(ModParams.ModParamManagement)
        self.mod.declaration = str(ROOT / 'mod_params.json')
        self.mod.filename = str(Path(directory) / 'variables.cfg')
        self.mod.printer = SimpleNamespace(command_error=RuntimeError)
        self.mod.gcode = SimpleNamespace(error=RuntimeError)
        self.mod.reactor = SimpleNamespace(register_callback=lambda callback: None)
        self.mod.changes_gcode_present = False
        self.mod._load_declaration()
        self.mod._reload()
        self.mod.variables.update(bed_mesh_validation_action=action,
                                  bed_mesh_validation_z_offset_mode=offset_mode, z_offset=offset)
        self.mod._save_all()
        self.initial_file = Path(self.mod.filename).read_bytes()

        self.move = GCodeMove.GCodeMove.__new__(GCodeMove.GCodeMove)
        self.move.Coord = Coord
        self.move.absolute_coord = self.move.absolute_extrude = True
        self.move.base_position = [0., 0., offset, 0.]
        self.move.homing_position = [0., 0., offset, 0.]
        self.move.last_position = [0., 0., 10., 0.]
        self.move.speed = 25.
        self.move.speed_factor = 1. / 60.
        self.move.extrude_factor = 1.
        self.move.saved_states = {}
        self.move.move_with_transform = lambda position, speed: None
        self.mesh = dict(profile_name='auto', profiles={'auto': {}}, mesh_min=(-100., -100.),
                         mesh_max=(100., 100.), mesh_matrix=matrix or [[0.] * 5 for _ in range(5)])
        self.saved_mesh = dict(self.mesh)
        self.probe = dict(last_z_result=0.)
        self.probe_count = 0
        self.fail_probe_at = None
        self.fail_calibration = False
        self.config_settings = {}
        self.temperatures = dict(extruder=150., heater_bed=60.)
        self.profile_copies = []
        self.expected = []
        self.shifts = list(shifts if shifts is not None else [-0.3] * 5)
        self.calibrated_profiles = []

    def context(self):
        printer = dict(self.variables)
        printer['filament_switch_sensor e0_sensor'] = {'filament_detected': True}
        printer.update(
            mod_params={'variables': self.mod.variables},
            gcode_move=self.move.get_status(), bed_mesh=self.mesh,
            probe=self.probe, configfile={'config': {'probe': {'z_offset': '0.7'}}, 'settings': self.config_settings},
            extruder={'target': self.temperatures['extruder']},
            heater_bed={'target': self.temperatures['heater_bed']},
            toolhead={'position': Coord(*self.move.last_position),
                      'axis_maximum': Coord(110., 110., 220., 0.)},
        )
        return dict(printer=printer, params={}, rawparams='',
                    action_respond_info=lambda message: '')

    @property
    def offset(self):
        return self.move.homing_position[2]

    @property
    def offset_before(self):
        return self.variables['gcode_macro _CHECK_BED_MESH']['offset_before']

    def run(self, name, **params):
        if name == '_RAISE_WITH_PRINT_CANCEL':
            raise Cancelled(params['MSG'])
        # Keep unrelated heater/service/stock-bridge commands out of the harness.
        if name in {'_PRINT_STATUS', '_CANCEL_DELAYED_COMMANDS', '_ENSURE_SERVICES_STARTED'}:
            return
        context = self.context()
        context['params'] = params
        context['rawparams'] = ' '.join(f'{key}={value}' for key, value in params.items())
        script = self.macros[name].render(context)
        lines = [line.strip() for line in script.splitlines()
                 if line.strip() and not line.lstrip().startswith(('#', ';'))]
        # PROBE returns an absolute toolhead position. Derive the scripted
        # contact heights from each generated expected-height command.
        if name == '_CHECK_BED_MESH':
            self.expected = [float(self.parse(line)[1]['EXPECTED'])
                             for line in lines if line.startswith('_CHECK_BED_MESH_VERIFY ')]
        for line in lines:
            self.execute(line)

    @staticmethod
    def parse(line):
        words = shlex.split(line.split(' ;', 1)[0])
        return words[0], dict(word.split('=', 1) for word in words[1:] if '=' in word)

    def execute(self, line):
        name, params = self.parse(line)
        self.trace.append((name, params))
        if name == 'SET_GCODE_VARIABLE':
            self.variables['gcode_macro ' + params['MACRO']][params['VARIABLE']] = ast.literal_eval(params['VALUE'])
        elif name == '_SET_GCODE_OFFSET':
            self.move.cmd_SET_GCODE_OFFSET(Command(params))
        elif name in {'SAVE_GCODE_STATE', 'RESTORE_GCODE_STATE', 'G90', 'G92'}:
            getattr(self.move, 'cmd_' + name)(Command(params))
        elif name == 'G1':
            # Klipper's native parser takes X5 rather than X=5.
            words = line.split(';', 1)[0].split()[1:]
            move_params = {word[0]: word[1:] for word in words}
            self.trace[-1] = (name, move_params)
            self.move.cmd_G1(Command(move_params))
        elif name == 'PROBE':
            self.probe_count += 1
            if self.probe_count == self.fail_probe_at:
                raise RuntimeError('Probe samples exceed samples_tolerance')
            self.probe['last_z_result'] = self.expected.pop(0) + 0.7 + self.shifts.pop(0)
        elif name == 'BED_MESH_CLEAR':
            self.mesh = dict(profile_name='', mesh_matrix=[], profiles=self.saved_mesh['profiles'])
        elif name == 'BED_MESH_PROFILE':
            if 'LOAD' in params:
                self.mesh = dict(self.saved_mesh, profile_name=params['LOAD'])
            elif 'SAVE' in params:
                self.profile_copies.append((self.mesh['profile_name'], params['SAVE']))
                self.saved_mesh['profiles'][params['SAVE']] = {}
        elif name == 'BED_MESH_CALIBRATE':
            if self.fail_calibration:
                raise RuntimeError('No trigger on probe after full movement')
            self.calibrated_profiles.append(params['PROFILE'])
            self.mesh = dict(self.saved_mesh, profile_name=params['PROFILE'])
        elif name == '_WAIT_TEMPERATURE':
            heater = 'extruder' if params['CMD'] == 'M104' else 'heater_bed'
            self.temperatures[heater] = float(params['VALUE'])
        elif name == 'SET_MOD':
            self.mod.cmd_SET_MOD(Command(params))
        elif name in {'_CHECK_BED_MESH_PROBE', '_CHECK_BED_MESH_VERIFY',
                      '_CHECK_BED_MESH_HANDLE_FAIL', '_CHECK_BED_MESH_APPLY_OFFSET',
                      '_RESET_BED_MESH_OFFSET',
                      '_RAISE_WITH_PRINT_CANCEL', '_START_PRINT_PREPARE',
                      'LOAD_GCODE_OFFSET', 'SET_GCODE_OFFSET', 'MOVE_SAFE',
                      '_FULL_BED_LEVEL', '_PREPARE_LEVELING'}:
            self.run(name, **params)


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def harness(self, **kwargs):
        return Harness(self.temp.name, **kwargs)

    def test_all_shared_templates_compile(self):
        self.harness()

    def test_new_and_existing_enum_defaults_load_without_saved_keys(self):
        harness = self.harness()
        parameter = harness.mod.params_map['bed_mesh_validation_action']
        self.assertEqual(harness.mod._load_param(parameter, None), 0)
        self.assertEqual(harness.mod._transform(parameter, None), 'CANCEL')
        for name, value in {'CANCEL': 0, 'RECALIBRATE': 1, 'Z_OFFSET': 2}.items():
            self.assertEqual(harness.mod._load_param(parameter, name), value)
        with self.assertRaises(KeyError):
            harness.mod._load_param(parameter, 'INVALID')
        mode = harness.mod.params_map['bed_mesh_validation_z_offset_mode']
        self.assertEqual(harness.mod._load_param(mode, None), 0)
        self.assertEqual(harness.mod._load_param(mode, 'ADJUST'), 0)
        self.assertEqual(harness.mod._load_param(mode, 'REPLACE'), 1)
        harness.mod.cmd_SET_MOD(Command({'PARAM': mode.key, 'VALUE': 'REPLACE'}))
        harness.mod._reload()
        self.assertEqual(harness.mod.variables[mode.key], 1)

    def test_signed_correction_preserves_user_offset_and_file(self):
        for shift in [-0.3, 0.3]:
            for offset in [-0.15, 0., 0.4]:
                with self.subTest(shift=shift, offset=offset):
                    harness = self.harness(action=2, offset=offset, shifts=[shift] * 5)
                    harness.run('_CHECK_BED_MESH')
                    self.assertAlmostEqual(harness.offset, offset + shift)
                    self.assertAlmostEqual(harness.offset_before, offset)
                    self.assertEqual(Path(harness.mod.filename).read_bytes(), harness.initial_file)
                    self.assertEqual(harness.mesh['profile_name'], 'auto')

    def test_mean_uses_sign_and_all_five_samples(self):
        harness = self.harness(action=2, shifts=[0.3, 0.4, 0.35, 0.45, 0.4])
        harness.run('_CHECK_BED_MESH')
        self.assertAlmostEqual(harness.offset, 0.05 + 0.38)

    def test_replace_uses_correction_as_offset_and_restores_original_without_saving(self):
        for shift in [-0.3, 0.3]:
            for offset in [-0.15, 0., 0.4]:
                with self.subTest(shift=shift, offset=offset):
                    harness = self.harness(action=2, offset_mode=1, offset=offset, shifts=[shift] * 5)
                    harness.run('_CHECK_BED_MESH')
                    self.assertAlmostEqual(harness.offset, shift)
                    self.assertAlmostEqual(harness.offset_before, offset)
                    self.assertEqual(Path(harness.mod.filename).read_bytes(), harness.initial_file)
                    harness.run('_RESET_BED_MESH_OFFSET')
                    self.assertAlmostEqual(harness.offset, offset)
                    self.assertIsNone(harness.offset_before)

    def test_nonuniform_shift_cancels_in_both_offset_modes(self):
        for mode in (0, 1):
            for shifts in ([.5, -.5, .25, -.25, 0.], [.3, .8, .5, .6, .4]):
                with self.subTest(mode=mode, shifts=shifts):
                    harness = self.harness(action=2, offset_mode=mode, offset=.2, shifts=shifts)
                    with self.assertRaisesRegex(Cancelled, 'shape changed'):
                        harness.run('_CHECK_BED_MESH')
                    self.assertAlmostEqual(harness.offset, .2)
                    self.assertIsNone(harness.offset_before)
                    self.assertEqual(Path(harness.mod.filename).read_bytes(), harness.initial_file)

    def test_spread_must_be_strictly_below_requested_tolerance(self):
        for spread, accepted in ((.125, True), (.25, False), (.5, False)):
            for mode in (0, 1):
                with self.subTest(spread=spread, mode=mode):
                    harness = self.harness(action=2, offset_mode=mode, shifts=[.5] * 4 + [.5 + spread])
                    if accepted:
                        harness.run('_CHECK_BED_MESH', TOLERANCE='.25')
                        self.assertAlmostEqual(harness.offset, .5 + spread / 5 + (0. if mode else .05))
                    else:
                        with self.assertRaisesRegex(Cancelled, 'shape changed'):
                            harness.run('_CHECK_BED_MESH', TOLERANCE='.25')
                        self.assertAlmostEqual(harness.offset, .05)

    def test_oversized_correction_or_result_cancels(self):
        cases = ((0, 0., 2.01), (1, 0., -2.01), (0, 1.9, .3), (0, -1.9, -.3), (1, -2., 2.01))
        for mode, offset, shift in cases:
            with self.subTest(mode=mode, offset=offset, shift=shift):
                harness = self.harness(action=2, offset_mode=mode, offset=offset, shifts=[shift] * 5)
                with self.assertRaisesRegex(Cancelled, 'Z-offset limit'):
                    harness.run('_CHECK_BED_MESH')
                self.assertAlmostEqual(harness.offset, offset)
                self.assertIsNone(harness.offset_before)
                self.assertEqual(Path(harness.mod.filename).read_bytes(), harness.initial_file)

    def test_configured_offset_limit_is_respected(self):
        for mode in (0, 1):
            harness = self.harness(action=2, offset_mode=mode, shifts=[.6] * 5)
            harness.config_settings['feather_screen'] = {'z_offset_limit': .5}
            with self.assertRaisesRegex(Cancelled, 'Z-offset limit'):
                harness.run('_CHECK_BED_MESH')
            self.assertAlmostEqual(harness.offset, .05)

    def test_recalibration_prepares_a_hot_nozzle_through_the_standard_path(self):
        harness = self.harness(action=1)
        harness.temperatures['extruder'] = 240.
        harness.run('_CHECK_BED_MESH')
        names = [name for name, params in harness.trace]
        self.assertLess(names.index('_FULL_BED_LEVEL'), names.index('_PREPARE_LEVELING'))
        self.assertLess(names.index('_PREPARE_LEVELING'), names.index('CLEAR_NOZZLE'))
        self.assertLess(names.index('CLEAR_NOZZLE'), names.index('BED_MESH_CALIBRATE'))
        cleaning = next(params for name, params in harness.trace if name == 'CLEAR_NOZZLE')
        self.assertEqual(float(cleaning['EXTRUDER_TEMP']), 240.)
        self.assertEqual(harness.profile_copies, [('auto', 'auto_prev')])

    def test_disabled_cleaning_cools_and_tares_before_recalibration(self):
        harness = self.harness(action=1)
        harness.temperatures['extruder'] = 240.
        harness.mod.variables.update(disable_cleaning=True, clear_cooldown_temp=150.)
        harness.run('_CHECK_BED_MESH')
        cooling = next(index for index, (name, params) in enumerate(harness.trace)
                       if name == '_WAIT_TEMPERATURE' and params['CMD'] == 'M104')
        tare = next(index for index, (name, params) in enumerate(harness.trace)
                    if index > cooling and name == 'LOAD_CELL_TARE')
        calibration = next(index for index, (name, params) in enumerate(harness.trace)
                           if name == 'BED_MESH_CALIBRATE')
        self.assertEqual(float(harness.trace[cooling][1]['VALUE']), 150.)
        self.assertLess(cooling, tare)
        self.assertLess(tare, calibration)
        self.assertAlmostEqual(harness.offset, .05)

    def test_correction_at_offset_limit_is_allowed(self):
        for mode in (0, 1):
            for shift in (-2., 2.):
                harness = self.harness(action=2, offset_mode=mode, offset=0., shifts=[shift] * 5)
                harness.run('_CHECK_BED_MESH')
                self.assertAlmostEqual(harness.offset, shift)

    def test_probe_failure_preserves_user_offset_and_loaded_mesh(self):
        for failed_probe in (1, 3, 5):
            harness = self.harness(action=2, offset=-.15)
            harness.mod.variables['load_zoffset'] = 0
            original_mesh = dict(harness.mesh)
            harness.fail_probe_at = failed_probe
            with self.assertRaisesRegex(RuntimeError, 'samples_tolerance'):
                harness.run('_CHECK_BED_MESH')
            self.assertAlmostEqual(harness.offset, -.15)
            self.assertEqual(harness.mesh, original_mesh)
            self.assertIsNone(harness.offset_before)
            harness.run('_START_PRINT_PREPARE')
            self.assertAlmostEqual(harness.offset, -.15)
            self.assertEqual(Path(harness.mod.filename).read_bytes(), harness.initial_file)

    def test_recalibration_failure_preserves_user_offset(self):
        harness = self.harness(action=1, offset=-.15)
        harness.mod.variables.update(load_zoffset=0, disable_cleaning=True)
        harness.fail_calibration = True
        with self.assertRaisesRegex(RuntimeError, 'No trigger'):
            harness.run('_CHECK_BED_MESH')
        harness.run('_START_PRINT_PREPARE')
        self.assertAlmostEqual(harness.offset, -.15)
        self.assertEqual(Path(harness.mod.filename).read_bytes(), harness.initial_file)

    def test_replace_repeated_checks_and_mode_changes_do_not_accumulate(self):
        harness = self.harness(action=2, offset_mode=1)
        harness.run('_CHECK_BED_MESH')
        self.assertAlmostEqual(harness.offset, -0.3)
        for mode, shift, expected in [(1, -0.3, -0.3), (0, 0.4, 0.45), (1, -0.25, -0.25), (1, 0., 0.05)]:
            harness.mod.variables['bed_mesh_validation_z_offset_mode'] = mode
            harness.shifts = [shift] * 5
            harness.run('_CHECK_BED_MESH')
            self.assertAlmostEqual(harness.offset, expected)
        self.assertIsNone(harness.offset_before)

    def test_replace_cleanup_with_validation_disabled(self):
        for command in ['_STOP', '_START_PRINT_PREPARE']:
            harness = self.harness(action=2, offset_mode=1)
            harness.mod.variables['load_zoffset'] = 0
            harness.run('_CHECK_BED_MESH')
            harness.mod.variables['bed_mesh_validation'] = 0
            harness.run(command)
            self.assertAlmostEqual(harness.offset, 0.05)
            self.assertIsNone(harness.offset_before)
            self.assertEqual(Path(harness.mod.filename).read_bytes(), harness.initial_file)

    def test_offset_changes_during_compensation_are_not_saved_and_are_reverted(self):
        changes = [('SET_GCODE_OFFSET', {'Z_ADJUST': '0.02'}, 0.02),
                   ('SET_GCODE_OFFSET', {'Z': '0.12'}, None),
                   ('_SET_GCODE_OFFSET', {'Z_ADJUST': '0.02'}, 0.02),
                   ('LOAD_GCODE_OFFSET', {}, None)]
        for mode in (0, 1):
            for command, params, adjustment in changes:
                with self.subTest(mode=mode, command=command, params=params):
                    harness = self.harness(action=2, offset_mode=mode)
                    harness.run('_CHECK_BED_MESH')
                    compensated = harness.offset
                    harness.execute(' '.join([command] + [f'{key}={value}' for key, value in params.items()]))
                    if adjustment is not None:
                        self.assertAlmostEqual(harness.offset, compensated + adjustment)
                    self.assertAlmostEqual(harness.mod.variables['z_offset'], 0.05)
                    harness.run('_STOP')
                    self.assertAlmostEqual(harness.offset, 0.05)
                    self.assertIsNone(harness.offset_before)
                    harness.mod._reload()
                    self.assertAlmostEqual(harness.mod.variables['z_offset'], 0.05)
                    self.assertEqual(Path(harness.mod.filename).read_bytes(), harness.initial_file)

    def test_offset_changes_are_saved_again_after_compensation_ends(self):
        harness = self.harness(action=2)
        harness.run('_CHECK_BED_MESH')
        harness.run('_STOP')
        harness.run('SET_GCODE_OFFSET', Z_ADJUST='0.02')
        self.assertAlmostEqual(harness.offset, 0.07)
        harness.mod._reload()
        self.assertAlmostEqual(harness.mod.variables['z_offset'], 0.07)

    def test_replace_mode_only_affects_z_offset_action(self):
        for action in [0, 1, 2]:
            harness = self.harness(action=action, offset_mode=1, shifts=[0.05] * 5)
            harness.run('_CHECK_BED_MESH')
            self.assertAlmostEqual(harness.offset, 0.05)
            self.assertIsNone(harness.offset_before)
        for action in [0, 1]:
            harness = self.harness(action=action, offset_mode=1)
            if action == 0:
                with self.assertRaises(Cancelled):
                    harness.run('_CHECK_BED_MESH')
            else:
                harness.run('_CHECK_BED_MESH')
                self.assertEqual(harness.calibrated_profiles, ['auto'])
                self.assertEqual(harness.profile_copies, [('auto', 'auto_prev')])
            self.assertAlmostEqual(harness.offset, 0.05)
            self.assertIsNone(harness.offset_before)

    def test_default_cancel_reports_absolute_mean(self):
        harness = self.harness(shifts=[0.5, -0.3, 0.1, -0.1, 0.3])
        with self.assertRaisesRegex(Cancelled, 'Avg. diff: 0.26 mm'):
            harness.run('_CHECK_BED_MESH')
        self.assertAlmostEqual(harness.offset, 0.05)
        self.assertIsNone(harness.offset_before)

    def test_within_tolerance_does_not_cancel_or_recalibrate(self):
        for action in [0, 1]:
            harness = self.harness(action=action, shifts=[0.1] * 5)
            harness.run('_CHECK_BED_MESH')
            self.assertAlmostEqual(harness.offset, 0.05)
            self.assertIsNone(harness.offset_before)
            self.assertEqual(harness.calibrated_profiles, [])

    def test_correction_threshold_is_independent_of_validation_tolerance(self):
        for mode in (0, 1):
            for shift in (-.3, -.1, -.050001, -.05, -.049999, 0., .049999, .05, .050001, .1, .3):
                with self.subTest(mode=mode, shift=shift):
                    harness = self.harness(action=2, offset_mode=mode, offset=.2, shifts=[shift] * 5)
                    harness.run('_CHECK_BED_MESH')
                    applied = abs(shift) > .05
                    expected = (shift if mode else .2 + shift) if applied else .2
                    self.assertAlmostEqual(harness.offset, expected)
                    self.assertEqual(harness.offset_before, .2 if applied else None)
                    self.assertEqual(harness.calibrated_profiles, [])
                    self.assertEqual(Path(harness.mod.filename).read_bytes(), harness.initial_file)
                    harness.run('_RESET_BED_MESH_OFFSET')
                    self.assertAlmostEqual(harness.offset, .2)
                    self.assertIsNone(harness.offset_before)

    def test_correction_threshold_uses_signed_mean_of_all_points(self):
        for shifts, correction in (([.04, .06, .08, .1, .12], .08),
                                   ([-.08, -.06, 0., .06, .08], None)):
            with self.subTest(shifts=shifts):
                harness = self.harness(action=2, shifts=shifts)
                harness.run('_CHECK_BED_MESH')
                self.assertAlmostEqual(harness.offset, .05 + (correction or 0.))
                self.assertEqual(harness.offset_before, .05 if correction is not None else None)

    def test_nonuniform_shift_within_point_tolerance_still_cancels(self):
        for mode in (0, 1):
            with self.subTest(mode=mode):
                harness = self.harness(action=2, offset_mode=mode, shifts=[-.15, .15, 0., 0., 0.])
                with self.assertRaisesRegex(Cancelled, 'shape changed'):
                    harness.run('_CHECK_BED_MESH')
                self.assertAlmostEqual(harness.offset, .05)
                self.assertIsNone(harness.offset_before)

    def test_tolerance_boundary_and_small_mesh(self):
        harness = self.harness(action=2, shifts=[0.25] * 5, matrix=[[0.] * 3 for _ in range(3)])
        harness.run('_CHECK_BED_MESH', TOLERANCE='0.25')
        self.assertAlmostEqual(harness.offset, 0.3)

    def test_recalibration_preserves_offset_and_profile_without_save(self):
        harness = self.harness(action=1, offset=-0.15)
        harness.run('_CHECK_BED_MESH')
        self.assertEqual(harness.calibrated_profiles, ['auto'])
        self.assertEqual(harness.profile_copies, [('auto', 'auto_prev')])
        self.assertAlmostEqual(harness.offset, -0.15)
        self.assertIsNone(harness.offset_before)
        self.assertFalse(any(name in {'SAVE_CONFIG', 'SET_MOD', 'SET_GCODE_OFFSET'}
                             for name, params in harness.trace))
        self.assertEqual(Path(harness.mod.filename).read_bytes(), harness.initial_file)

    def test_repeated_check_does_not_accumulate(self):
        harness = self.harness(action=2)
        harness.run('_CHECK_BED_MESH')
        harness.shifts = [-0.3] * 5
        harness.run('_CHECK_BED_MESH')
        self.assertAlmostEqual(harness.offset, -0.25)
        harness.shifts = [0.] * 5
        harness.run('_CHECK_BED_MESH')
        self.assertAlmostEqual(harness.offset, 0.05)
        self.assertIsNone(harness.offset_before)

    def test_print_end_and_next_start_remove_only_correction(self):
        for command in ['_STOP', '_START_PRINT_PREPARE']:
            harness = self.harness(action=2)
            harness.mod.variables.update(display=0, load_zoffset=0)
            harness.run('_CHECK_BED_MESH')
            harness.run(command)
            self.assertAlmostEqual(harness.offset, 0.05)
            self.assertIsNone(harness.offset_before)

    def test_next_start_removes_correction_when_validation_is_disabled(self):
        harness = self.harness(action=2)
        harness.mod.variables.update(bed_mesh_validation=1, load_zoffset=0)
        harness.run('_CHECK_BED_MESH')
        harness.mod.variables['bed_mesh_validation'] = 0
        probe_count = harness.probe_count
        harness.run('_START_PRINT_PREPARE')
        self.assertAlmostEqual(harness.offset, 0.05)
        self.assertIsNone(harness.offset_before)
        self.assertEqual(harness.probe_count, probe_count)
        self.assertEqual(Path(harness.mod.filename).read_bytes(), harness.initial_file)

    def test_validation_z_moves_use_the_configured_safe_z(self):
        harness = self.harness(action=2)
        harness.mod.variables['safe_z'] = 8.
        harness.run('_CHECK_BED_MESH')
        z_moves = [params['Z'] for name, params in harness.trace if name == 'G1' and 'Z' in params]
        self.assertEqual(z_moves, ['8.0'] + ['8.0', '3', '8.0'] * 5)
        self.assertAlmostEqual(harness.offset, -0.25)

    def test_aborted_print_without_stop_is_restored_by_next_print(self):
        harness = self.harness(action=2)
        harness.mod.variables['load_zoffset'] = 0
        harness.run('_CHECK_BED_MESH')
        harness.run('SET_GCODE_OFFSET', Z_ADJUST='0.02')
        # No _STOP: the next print restores the offset before compensation.
        harness.run('_START_PRINT_PREPARE')
        self.assertAlmostEqual(harness.offset, 0.05)
        harness.shifts = [-0.2] * 5
        harness.run('_CHECK_BED_MESH')
        self.assertAlmostEqual(harness.offset, -0.15)
        self.assertAlmostEqual(harness.offset_before, 0.05)

    def test_expected_values_follow_original_grid_and_bottom_row(self):
        matrix = [[row * 0.1 + col * 0.01 for col in range(5)] for row in range(5)]
        harness = self.harness(action=2, matrix=matrix, shifts=[0.] * 5)
        harness.run('_CHECK_BED_MESH')
        verifies = [float(params['EXPECTED']) for name, params in harness.trace
                    if name == '_CHECK_BED_MESH_VERIFY']
        self.assertEqual(verifies, [matrix[3][0], matrix[0][0],
                                   matrix[0][3], matrix[0][4], matrix[3][4]])
        probes = [params for name, params in harness.trace if name == '_CHECK_BED_MESH_PROBE']
        self.assertEqual(float(probes[0]['Y']), 50.)
        self.assertEqual(float(probes[2]['X']), 50.)
        self.assertIsNone(harness.offset_before)

    def test_no_loaded_mesh_skips_probing(self):
        harness = self.harness(action=2)
        harness.mesh['profile_name'] = ''
        harness.run('_CHECK_BED_MESH')
        self.assertEqual(harness.probe_count, 0)
        self.assertIsNone(harness.offset_before)


if __name__ == '__main__':
    unittest.main()
