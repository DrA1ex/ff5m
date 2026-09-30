# Behavioral coverage for legacy Klipper homing backports and diagnostics.
#
# Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import importlib.util
import logging
import pathlib
import sys
import types
from unittest import mock

import pytest

ROOT = pathlib.Path(__file__).parents[1]


def load_module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HOMING = load_module('backport_homing', '.py/klipper/patches/extras/homing.py')
MOVE = load_module('backport_gcode_move', '.py/klipper/patches/extras/gcode_move.py')
GCODE = load_module('backport_gcode', '.py/klipper/patches/gcode.py')


@pytest.fixture
def protocol():
    native = mock.Mock()
    ffi = types.SimpleNamespace(gc=lambda obj, free: obj)
    deps = {name: types.ModuleType(name) for name in
            ('serialhdl', 'msgproto', 'pins', 'chelper', 'clocksync')}
    deps['serialhdl'].error = RuntimeError
    deps['chelper'].get_ffi = lambda: (ffi, native)
    with mock.patch.dict(sys.modules, deps):
        module = load_module('backport_mcu', '.py/klipper/patches/mcu.py')
    reactor = mock.Mock()
    printer = mock.Mock(command_error=RuntimeError)
    printer.get_reactor.return_value = reactor
    return types.SimpleNamespace(module=module, native=native,
                                 printer=printer, reactor=reactor)


class Mcu:
    def __init__(self, protocol, name, frequency=1000000):
        self.printer = protocol.printer
        self.name = name
        self.frequency = frequency
        self._serial = types.SimpleNamespace(serialqueue=object())
        self.callbacks = []
        self.commands = {}
        self.responses = {}
        self.oid = 0
        self.reason = 1 if name == 'eboard' else 3
        self.next_clock = 1200100
    def get_printer(self):
        return self.printer
    def get_name(self):
        return self.name
    def create_oid(self):
        self.oid += 1
        return self.oid
    def alloc_command_queue(self):
        return object()
    def register_config_callback(self, callback):
        self.callbacks.append(callback)
    def add_config_cmd(self, *args, **kwargs):
        pass
    def lookup_command(self, message, cq=None):
        command = mock.Mock()
        self.commands[message.split()[0]] = command
        return command
    def lookup_query_command(self, message, response, **kwargs):
        command = mock.Mock()
        if response.startswith('trsync_state'):
            command.send.side_effect = lambda args: {'trigger_reason': self.reason}
        else:
            command.send.side_effect = lambda args: {'next_clock': self.next_clock}
        self.commands[response.split()[0]] = command
        return command
    def lookup_command_tag(self, message):
        return len(message)
    def register_response(self, callback, name, oid):
        self.responses[name, oid] = callback
    def print_time_to_clock(self, time):
        return int(time * self.frequency)
    def seconds_to_clock(self, time):
        return int(time * self.frequency)
    def clock32_to_clock64(self, clock):
        return clock
    def clock_to_print_time(self, clock):
        return clock / self.frequency
    def is_fileoutput(self):
        return False


def endstop(protocol, multi=True, frequency=1000000):
    mcu = Mcu(protocol, 'eboard', frequency)
    motor = Mcu(protocol, 'mcu', frequency) if multi else mcu
    es = protocol.module.MCU_endstop(
        mcu, {'pin': 'PA15', 'pullup': False, 'invert': True})
    stepper = mock.Mock()
    stepper.get_mcu.return_value = motor
    stepper.get_name.return_value = 'stepper_x'
    stepper.get_oid.return_value = 10
    es.add_stepper(stepper)
    for chip in dict.fromkeys((mcu, motor)):
        for callback in chip.callbacks:
            callback()
    return es, mcu, motor, stepper


@pytest.mark.parametrize('timeout,report_ticks,phase_ticks',
                         [(.025, 7500, 3750), (.050, 15000, 7500)])
def test_multi_mcu_reports_are_staggered_without_delaying_setup(
        protocol, timeout, report_ticks, phase_ticks):
    protocol.module.TRSYNC_TIMEOUT = timeout
    es, mcu, motor, stepper = endstop(protocol)
    es.home_start(1., .000015, 4, .0001)
    for chip, phase in ((mcu, 0), (motor, phase_ticks)):
        start = chip.commands['trsync_start'].send.call_args
        assert start.args[0][1:] == [1000000 + phase, report_ticks, 2]
        assert start.kwargs == {'reqclock': 1000000}
        expire = chip.commands['trsync_set_timeout'].send.call_args
        assert expire.args[0][1] == 1000000 + int(timeout * 1000000)
        assert expire.kwargs == {'reqclock': 1000000}
    setups = protocol.native.trdispatch_mcu_setup.call_args_list
    assert [call.args[-1] for call in setups] == [int(report_ticks * .8)] * 2
    motor.commands['stepper_stop_on_trigger'].send.assert_called_once()
    assert mcu.commands['endstop_home'].send.call_args.kwargs == {'reqclock': 1000000}


def test_single_mcu_keeps_longer_watchdog_and_zero_phase(protocol):
    protocol.module.TRSYNC_TIMEOUT = .050
    es, mcu, motor, stepper = endstop(protocol, multi=False)
    es.home_start(1., .000015, 4, .0001)
    assert mcu.commands['trsync_start'].send.call_args.args[0][1:] == [1000000, 75000, 2]
    assert mcu.commands['trsync_set_timeout'].send.call_args.args[0][1] == 1250000


def test_extension_threshold_uses_integer_report_ticks(protocol):
    protocol.module.TRSYNC_TIMEOUT = .050
    es, mcu, motor, stepper = endstop(protocol, frequency=1337)
    es.home_start(1., .000015, 4, .001)
    assert motor.commands['trsync_start'].send.call_args.args[0][1:3] == [1347, 20]
    assert protocol.native.trdispatch_mcu_setup.call_args.args[-1] == 16


@pytest.mark.parametrize('primary', [1, 2, 3, 4])
@pytest.mark.parametrize('secondary', [1, 2, 3, 4])
def test_final_reasons_remain_authoritative(protocol, caplog, primary, secondary):
    caplog.set_level(logging.INFO)
    es, mcu, motor, stepper = endstop(protocol)
    es.home_start(1., .000015, 4, .0001)
    mcu.reason, motor.reason = primary, secondary
    # Completion alone must not turn a failed MCU reason into a valid hit.
    protocol.reactor.completion.return_value.wait.return_value = False
    result = es.home_wait(2.)
    expected = -1. if 2 in (primary, secondary) else (1.2 if primary == 1 else 0.)
    assert result == pytest.approx(expected)
    assert mcu.responses['trsync_state', es._trsyncs[0].get_oid()] is None
    protocol.native.trdispatch_stop.assert_called_once()
    stepper.note_homing_end.assert_called_once()
    assert mcu.commands['endstop_home'].send.call_args.args[0] == [es._oid, 0, 0, 0, 0, 0, 0, 0]
    assert 'Homing endstop stop:' in caplog.text
    assert '1=hit 2=timeout 3=host 4=past_end' in caplog.text
    if result > 0:
        assert 'next_clock=1200100' in caplog.text
        assert 'trigger_time=1.200000' in caplog.text
    else:
        mcu.commands['endstop_state'].send.assert_not_called()


def test_periodic_reports_are_not_logged_and_shutdown_releases_wait(protocol, caplog):
    caplog.set_level(logging.INFO)
    es, mcu, motor, stepper = endstop(protocol)
    completion = es.home_start(1., .000015, 4, .0001)
    caplog.clear()
    callback = mcu.responses['trsync_state', es._trsyncs[0].get_oid()]
    callback({'can_trigger': 1, 'clock': 1100000})
    assert not caplog.records
    es._trsyncs[0]._shutdown()
    completion.complete.assert_called_once_with(False)


def test_past_end_requests_the_legacy_non_hit_reason(protocol):
    es, mcu, motor, stepper = endstop(protocol)
    es.home_start(1., .000015, 4, .0001)
    ts = es._trsyncs[0]
    ts.set_home_end_time(2.)
    mcu.responses['trsync_state', ts.get_oid()]({'can_trigger': 1, 'clock': 2000000})
    mcu.commands['trsync_trigger'].send.assert_called_once_with([ts.get_oid(), 4])


class HomingMotion:
    def __init__(self, trigger_time=1.5, travel_steps=1000, failure=None):
        self.position = [10., 20., 5., 0.]
        self.time = 1.
        self.printer = mock.Mock(command_error=RuntimeError)
        self.printer.get_start_args.return_value = {}
        self.steppers = []
        for name, count, sign in (('stepper_x', 3000, 1), ('stepper_y', -1000, -1)):
            s = mock.Mock()
            s.get_name.return_value = name
            s.get_step_dist.return_value = .01
            s.get_mcu_position.return_value = count
            s.get_commanded_position.side_effect = lambda sign=sign: self.position[0] + sign * self.position[1]
            s.calc_position_from_coord.side_effect = lambda p, sign=sign: p[0] + sign * p[1]
            s.get_past_mcu_position.return_value = count + travel_steps
            self.steppers.append(s)
        self.kin = mock.Mock()
        self.kin.get_steppers.return_value = self.steppers
        self.kin.calc_position.side_effect = lambda p: [
            .5 * (p['stepper_x'] + p['stepper_y']),
            .5 * (p['stepper_x'] - p['stepper_y']), 5.]
        self.toolhead = mock.Mock()
        self.toolhead.get_position.side_effect = lambda: list(self.position)
        self.toolhead.get_kinematics.return_value = self.kin
        self.toolhead.get_last_move_time.side_effect = lambda: self.time
        self.toolhead.set_position.side_effect = lambda p, **kw: setattr(self, 'position', list(p))
        def drip(pos, speed, completion):
            self.position = list(pos)
            self.time = 2.
            for s in self.steppers:
                s.get_mcu_position.return_value = s.get_past_mcu_position.return_value + 2
            if failure:
                raise RuntimeError(failure)
        self.toolhead.drip_move.side_effect = drip
        self.endstop = mock.Mock()
        self.endstop.get_steppers.return_value = self.steppers
        self.endstop.home_wait.return_value = trigger_time
        self.move = HOMING.HomingMove(self.printer, [(self.endstop, 'x')], self.toolhead)


@pytest.mark.parametrize('probe,target,halt', [
    (False, [110., 20., 5., 0.], [110.02, 20., 5., 0.]),
    (True, [20., 20., 5., 0.], [20.02, 20., 5., 0.])])
def test_corexy_trigger_and_halt_coordinates_are_preserved(caplog, probe, target, halt):
    caplog.set_level(logging.INFO)
    motion = HomingMotion()
    result = motion.move.homing_move([110., 20., 5., 0.], 50., probe_pos=probe)
    assert result == pytest.approx(target)
    assert motion.position == pytest.approx(halt)
    assert 'travel_steps=1000 over_steps=2' in caplog.text
    assert 'valid_trigger=True' in caplog.text
    assert 'error=None' in caplog.text


@pytest.mark.parametrize('time,failure,expected', [
    (-1., None, 'Communication timeout'),
    (0., None, 'No trigger'),
    (1.5, 'cancelled', 'cancelled')])
def test_homing_errors_are_logged_and_still_raised(caplog, time, failure, expected):
    caplog.set_level(logging.INFO)
    motion = HomingMotion(trigger_time=time, failure=failure)
    with pytest.raises(RuntimeError, match=expected):
        motion.move.homing_move([110., 20., 5., 0.], 50.)
    assert expected in caplog.text
    motion.printer.send_event.assert_any_call('homing:homing_move_end', motion.move)


def test_immediate_trigger_guard_is_not_changed():
    motion = HomingMotion(travel_steps=0)
    motion.move.homing_move([110., 20., 5., 0.], 50.)
    assert motion.move.check_no_movement() == 'x'


def test_g28_failure_still_disables_motors(caplog):
    caplog.set_level(logging.INFO)
    motion = HomingMotion()
    gcode, enable = mock.Mock(), mock.Mock()
    motion.printer.lookup_object.side_effect = lambda name: {
        'gcode': gcode, 'toolhead': motion.toolhead, 'stepper_enable': enable}[name]
    motion.printer.is_shutdown.return_value = False
    motion.kin.home.side_effect = RuntimeError('Communication timeout')
    handler = HOMING.PrinterHoming(types.SimpleNamespace(get_printer=lambda: motion.printer))
    with pytest.raises(RuntimeError, match='Communication timeout'):
        handler.cmd_G28(GCODE.GCodeCommand(gcode, 'G28', 'G28 X', {'X': ''}, False))
    enable.motor_off.assert_called_once()
    assert 'Homing G28 failed:' in caplog.text


def test_state_restore_logs_old_base_without_changing_restoration(caplog):
    caplog.set_level(logging.INFO)
    gcode = mock.Mock(Coord=GCODE.Coord)
    toolhead = mock.Mock()
    toolhead.get_position.return_value = [110., 110., 220., 0.]
    printer = mock.Mock()
    printer.lookup_object.side_effect = lambda name: {'gcode': gcode, 'toolhead': toolhead}[name]
    move = MOVE.GCodeMove(types.SimpleNamespace(get_printer=lambda: printer))
    move._handle_ready()
    def command(name, params):
        return GCODE.GCodeCommand(gcode, name, name, params, False)
    move.cmd_G92(command('G92', {'X': '0', 'Y': '0'}))
    move.cmd_SAVE_GCODE_STATE(command('SAVE_GCODE_STATE', {'NAME': 'park_state'}))
    move._handle_home_rails_end(types.SimpleNamespace(get_axes=lambda: [0, 1, 2]), [])
    assert move.get_status()['base_position'][:3] == (0., 0., 0.)
    move.cmd_RESTORE_GCODE_STATE(command('RESTORE_GCODE_STATE', {'NAME': 'park_state'}))
    assert move.get_status()['base_position'][:3] == (110., 110., 0.)
    assert move.get_status()['gcode_position'][:3] == (0., 0., 220.)
    assert 'Homing gcode coordinates:' in caplog.text
    assert 'name=park_state base=[0.0, 0.0, 0.0] restored_base=[110.0, 110.0, 0.0]' in caplog.text
    toolhead.move.assert_not_called()
