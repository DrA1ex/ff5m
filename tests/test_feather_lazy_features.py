## Lazy product-feature runtime contracts for Feather.

import os
import pathlib
import subprocess
import sys
import types
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).parents[1]
PLUGINS = ROOT / ".py" / "klipper" / "plugins"
sys.path.insert(0, str(PLUGINS))

import feather_screen as FEATHER  # noqa: E402
from feather_feature_manager import (  # noqa: E402
    FeatureHostProxy, FeatureLoadError, FeatureSpec, LazyFeatureManager,
)
from ui.lazy import LazyModule, resolve_lazy_export  # noqa: E402


class SharedLazyImportTest(unittest.TestCase):
    def test_module_proxy_imports_only_on_first_attribute_access(self):
        module = types.SimpleNamespace(first=1, second=2)
        with mock.patch("ui.lazy.importlib.import_module",
                        return_value=module) as importer:
            proxy = LazyModule("test.lazy_target")
            importer.assert_not_called()

            self.assertEqual(proxy.first, 1)
            self.assertEqual(proxy.second, 2)

        importer.assert_called_once_with("test.lazy_target")

    def test_module_proxy_qualifies_name_internally(self):
        module = types.SimpleNamespace(value=3)
        with mock.patch("ui.lazy.importlib.import_module",
                        return_value=module) as importer:
            proxy = LazyModule("feature.runtime", package="extras")
            self.assertEqual(proxy.value, 3)

        importer.assert_called_once_with("extras.feature.runtime")

    def test_failed_proxy_import_can_be_retried(self):
        module = types.SimpleNamespace(value="ready")
        with mock.patch(
                "ui.lazy.importlib.import_module",
                side_effect=(ImportError("not ready"), module)) as importer:
            proxy = LazyModule("test.retry_target")
            with self.assertRaises(ImportError):
                proxy.value
            self.assertEqual(proxy.value, "ready")

        self.assertEqual(importer.call_count, 2)

    def test_lazy_export_resolves_alias_and_caches_public_value(self):
        namespace = {"__name__": "test.facade"}
        module = types.SimpleNamespace(INTERNAL=object())
        exports = {"PUBLIC": ("implementation", "INTERNAL")}
        with mock.patch("ui.lazy.importlib.import_module",
                        return_value=module) as importer:
            first = resolve_lazy_export(
                namespace, "PUBLIC", exports, "test.facade")
            second = resolve_lazy_export(
                namespace, "PUBLIC", exports, "test.facade")

        self.assertIs(first, module.INTERNAL)
        self.assertIs(second, first)
        self.assertIs(namespace["PUBLIC"], first)
        importer.assert_called_once_with("test.facade.implementation")

    def test_unknown_lazy_export_uses_normal_module_attribute_contract(self):
        with self.assertRaisesRegex(
                AttributeError, "test.facade.*MISSING"):
            resolve_lazy_export(
                {"__name__": "test.facade"}, "MISSING", {})



class LazyImportContractTest(unittest.TestCase):
    def run_clean(self, source):
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(PLUGINS)
        subprocess.run([sys.executable, "-c", source], check=True,
                       env=environment)

    def test_screen_import_keeps_product_features_cold(self):
        self.run_clean("""
import sys
import feather_screen
blocked = (
    'feather_feature_ui_test', 'feather_ui_test',
    'feather_feature_filament', 'ff5m_ui.filament',
    'feather_feature_calibration', 'feather_feature_z',
    'feather_feature_extruder', 'feather_feature_settings',
    'feather_feature_benchmark', 'ff5m_ui.benchmark',
    'feather_z_calibration', 'feather_extruder_calibration',
    'feather_mod_settings',
)
assert not [name for name in blocked if name in sys.modules]
assert 'ff5m_ui.z_offset.constants' in sys.modules
assert not [name for name in sys.modules
            if name.startswith('ff5m_ui.z_offset.') and
            (name.endswith('.page') or name.endswith('.runtime') or
             name.endswith('.actions') or name.endswith('.common'))]
assert not [name for name in sys.modules
            if name.startswith('ff5m_ui.filament')]
assert 'ff5m_ui.heat.page' not in sys.modules
assert not [name for name in sys.modules
            if name.startswith('ff5m_ui.benchmark')]
assert '__getattr__' not in feather_screen.FeatherScreen.__dict__
assert not hasattr(feather_screen, 'ZCalibrationSession')
assert not hasattr(feather_screen, 'ExtruderCalibrationSession')
""")

    def test_controller_construction_keeps_product_features_cold(self):
        self.run_clean("""
import sys
import feather_screen

class Reactor:
    def monotonic(self):
        return 10.0
    def register_callback(self, callback, when=None):
        return None
    def register_async_callback(self, callback):
        return None

class GCode:
    def __init__(self):
        self.commands = {}
        self.immediate = set()
    def register_command(self, name, callback, desc=None):
        self.commands[name] = callback
    def register_immediate_command(self, name):
        assert name in self.commands
        self.immediate.add(name)

class Printer:
    def __init__(self):
        self.reactor = Reactor()
        self.gcode = GCode()
    def get_reactor(self):
        return self.reactor
    def lookup_object(self, name, default=None):
        return self.gcode if name == 'gcode' else default
    def register_event_handler(self, name, callback):
        pass

class Config:
    def __init__(self, blending=None, raster_acceleration=None):
        self.printer = Printer()
        self.blending = blending
        self.raster_acceleration = raster_acceleration
    def get_printer(self):
        return self.printer
    def getboolean(self, name, default=False):
        if name == 'blending' and self.blending is not None:
            return self.blending
        return default
    def getfloat(self, name, default=None, minval=None):
        return default
    def get(self, name, default=None):
        if name == 'raster_acceleration' and self.raster_acceleration is not None:
            return self.raster_acceleration
        return default
    def error(self, message):
        return ValueError(message)

feather_screen.FeatherScreen._start_pre_ready_ui = lambda self: None
controller = feather_screen.FeatherScreen(Config())
assert controller.feature_manager.loaded() == ()
assert controller.gcode.immediate == {'FEATHER_ABORT'}
assert controller.blending is True
assert controller.renderer.blending is True
assert controller.raster_acceleration == 'scalar'
assert controller.renderer.raster_acceleration == 'scalar'
disabled = feather_screen.FeatherScreen(Config(False))
assert disabled.blending is False
assert disabled.renderer.blending is False
accelerated = feather_screen.FeatherScreen(Config(raster_acceleration='NeOn'))
assert accelerated.raster_acceleration == 'neon'
assert accelerated.renderer.raster_acceleration == 'neon'
try:
    feather_screen.FeatherScreen(Config(raster_acceleration='dsp'))
except ValueError as error:
    assert 'raster_acceleration must be scalar or neon' in str(error)
else:
    raise AssertionError('invalid raster acceleration was accepted')
blocked = (
    'feather_feature_ui_test', 'feather_ui_test',
    'feather_feature_filament', 'ff5m_ui.filament',
    'feather_feature_calibration', 'feather_feature_z',
    'feather_feature_extruder', 'feather_feature_settings',
    'feather_feature_benchmark', 'ff5m_ui.benchmark',
    'feather_z_calibration', 'feather_extruder_calibration',
    'feather_mod_settings',
)
assert not [name for name in blocked if name in sys.modules]
assert 'ff5m_ui.z_offset.constants' in sys.modules
assert not [name for name in sys.modules
            if name.startswith('ff5m_ui.z_offset.') and
            (name.endswith('.page') or name.endswith('.runtime') or
             name.endswith('.actions') or name.endswith('.common'))]
assert not [name for name in sys.modules
            if name.startswith('ff5m_ui.filament')]
assert 'ff5m_ui.heat.page' not in sys.modules
assert not [name for name in sys.modules
            if name.startswith('ff5m_ui.benchmark')]
""")

    def test_features_load_sequentially_and_are_singletons(self):
        self.run_clean("""
import sys
import feather_screen
from feather_feature_manager import LazyFeatureManager
from ff5m_ui.screen import ScreenPage
from ui import FeatherRenderer

class Host:
    pass

host = Host()
host.renderer = FeatherRenderer()
host.renderer.send = lambda commands: None
host._setting = lambda key, default: default
host.feature_manager = LazyFeatureManager(host, feather_screen.FEATURE_SPECS)
manager = host.feature_manager
filament = manager.get('filament')
assert filament is manager.get('filament')
assert 'feather_feature_filament' in sys.modules
assert 'ff5m_ui.filament.runtime' in sys.modules
assert 'ff5m_ui.filament.material.page' not in sys.modules
assert 'ff5m_ui.filament.action.page' not in sys.modules
calibration = manager.get('calibration')
calibration.render(ScreenPage.CALIBRATION_HOME)
assert calibration is manager.get('calibration')
assert 'feather_feature_calibration' in sys.modules
assert 'feather_feature_z' not in sys.modules
assert 'feather_feature_extruder' not in sys.modules
assert 'feather_feature_settings' not in sys.modules
z_feature = manager.get('z')
assert z_feature is manager.get('z')
assert 'feather_z_calibration' in sys.modules
assert not [name for name in sys.modules
            if name.startswith('ff5m_ui.z_offset') and name.endswith('.page')]
z_feature.render(ScreenPage.SAFE_Z_BRIEFING)
pages = [name for name in sys.modules
         if name.startswith('ff5m_ui.z_offset') and name.endswith('.page')]
assert pages == ['ff5m_ui.z_offset.safe_briefing.page'], pages
manager.get('extruder')
assert 'feather_extruder_calibration' in sys.modules
manager.get('settings')
assert 'feather_mod_settings' in sys.modules
""")

    def test_z_offset_constants_do_not_load_actions_or_pages(self):
        self.run_clean("""
import sys
from ff5m_ui.z_offset.constants import (
    PAPER_DEFAULT_STEP, PAPER_STEPS, Z_WEIGHT_DANGER,
)

assert PAPER_DEFAULT_STEP in PAPER_STEPS
assert Z_WEIGHT_DANGER > 0
assert 'ff5m_ui.z_offset.constants' in sys.modules
assert not [name for name in sys.modules
            if name.startswith('ff5m_ui.z_offset.') and
            name != 'ff5m_ui.z_offset.constants']
""")

    def test_z_offset_public_actions_remain_lazy_exports(self):
        self.run_clean("""
import sys
import ff5m_ui.z_offset as z_offset

assert 'ff5m_ui.z_offset.actions' not in sys.modules
assert z_offset.Adjustment.CLOSER.value == 'closer'
assert 'ff5m_ui.z_offset.actions' in sys.modules
assert not [name for name in sys.modules
            if name.startswith('ff5m_ui.z_offset') and name.endswith('.page')]
""")

    def test_klipper_package_feature_uses_the_product_page_keys(self):
        self.run_clean("""
import os
import pathlib
import sys
import tempfile

plugins = pathlib.Path(os.environ['PYTHONPATH']).resolve()
with tempfile.TemporaryDirectory() as directory:
    os.symlink(str(plugins), os.path.join(directory, 'extras'))
    sys.path.insert(0, directory)
    import extras.feather_screen as feather_screen

    manager = feather_screen.LazyFeatureManager(
        object(), feather_screen.FEATURE_SPECS)
    feature = manager.get('calibration')
    feature_module = sys.modules[type(feature).__module__]
    assert feature_module.ScreenPage is feather_screen.ScreenPage
""")

    def test_klipper_fallback_feature_uses_the_product_page_keys(self):
        source = """
import os
import pathlib
import sys
import tempfile

plugins = pathlib.Path(sys.argv[1]).resolve()
with tempfile.TemporaryDirectory() as directory:
    os.symlink(str(plugins), os.path.join(directory, 'extras'))
    sys.path.insert(0, directory)
    import extras.feather_screen as feather_screen

    manager = feather_screen.LazyFeatureManager(
        object(), feather_screen.FEATURE_SPECS)
    feature = manager.get('calibration')
    feature_module = sys.modules[type(feature).__module__]
    assert feature_module.ScreenPage is feather_screen.ScreenPage
"""
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        result = subprocess.run(
            [sys.executable, "-c", source, str(PLUGINS)],
            env=environment, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class FeatureHostProxyTest(unittest.TestCase):
    def test_recovery_progress_reset_belongs_to_calibration_feature(self):
        from feather_feature_calibration import CalibrationFeature

        host = types.SimpleNamespace(heating_materials=())
        feature = CalibrationFeature(host)
        feature.calibration_progress_key = ("mesh", ("PREP", "LEVEL"))
        feature.calibration_seen_phases = {"PREP", "LEVEL"}

        feature.begin_recovery()

        self.assertEqual(feature.calibration_kind, "recovery")
        self.assertIsNone(feature.calibration_progress_key)
        self.assertEqual(feature.calibration_seen_phases, set())
        self.assertFalse(hasattr(host, "calibration_progress_key"))

    def test_shared_controller_fields_are_explicit_properties(self):
        host = types.SimpleNamespace(page=1, previous_page=0)
        proxy = FeatureHostProxy(host)

        proxy.page = 2
        proxy.previous_page = 1
        proxy.local_state = "feature-only"

        self.assertEqual(host.page, 2)
        self.assertEqual(host.previous_page, 1)
        self.assertFalse(hasattr(host, "local_state"))
        self.assertEqual(proxy.local_state, "feature-only")


class FeatureManagerTest(unittest.TestCase):
    def test_update_and_deactivate_do_not_load_cold_features(self):
        manager = LazyFeatureManager(object(), (
            FeatureSpec("cold", "module_that_must_not_load", "Factory", (1,)),
        ))

        manager.update(1.0)
        manager.notify("on_gcode_output", "message")
        self.assertEqual(manager.safety_active_reasons(1.0), ())
        self.assertEqual(manager.safety_armed_reasons(1, 1.0), ())
        manager.deactivate()

        self.assertEqual(manager.loaded(), ())

    def test_safety_hooks_only_query_loaded_feature_instances(self):
        module_name = "_feather_test_safety_feature"
        module = types.ModuleType(module_name)

        class Factory:
            def __init__(self, host):
                pass

            def safety_active_reasons(self, eventtime):
                return ("background-task",)

            def safety_armed_reasons(self, page, eventtime):
                return ("page-controls",)

        module.Factory = Factory
        sys.modules[module_name] = module
        try:
            manager = LazyFeatureManager(object(), (
                FeatureSpec("safe", module_name, "Factory", (7,)),
            ))
            self.assertEqual(manager.safety_active_reasons(1.0), ())
            self.assertEqual(manager.safety_armed_reasons(7, 1.0), ())
            manager.get("safe")
            self.assertEqual(manager.safety_active_reasons(1.0),
                             ("background-task",))
            self.assertEqual(manager.safety_armed_reasons(7, 1.0),
                             ("page-controls",))
        finally:
            sys.modules.pop(module_name, None)

    def test_failed_and_circular_factories_are_not_cached(self):
        failing_name = "_feather_test_failing_feature"
        circular_name = "_feather_test_circular_feature"
        failing = types.ModuleType(failing_name)
        circular = types.ModuleType(circular_name)

        class FailingFactory:
            def __init__(self, host):
                raise RuntimeError("factory failed")

        failing.Factory = FailingFactory
        sys.modules[failing_name] = failing
        try:
            manager = LazyFeatureManager(object(), (
                FeatureSpec("failing", failing_name, "Factory"),
            ))
            with self.assertRaisesRegex(FeatureLoadError, "factory failed"):
                manager.get("failing")
            self.assertIsNone(manager.peek("failing"))

            holder = {}

            class CircularFactory:
                def __init__(self, host):
                    holder["manager"].get("circular")

            circular.Factory = CircularFactory
            sys.modules[circular_name] = circular
            holder["manager"] = LazyFeatureManager(object(), (
                FeatureSpec("circular", circular_name, "Factory"),
            ))
            with self.assertRaisesRegex(FeatureLoadError, "Circular"):
                holder["manager"].get("circular")
            self.assertIsNone(holder["manager"].peek("circular"))
        finally:
            sys.modules.pop(failing_name, None)
            sys.modules.pop(circular_name, None)


class ControllerFeatureRoutingTest(unittest.TestCase):
    @staticmethod
    def controller():
        controller = FEATHER.FeatherScreen.__new__(FEATHER.FeatherScreen)
        controller.reactor = type("Reactor", (), {
            "monotonic": lambda self: 100.0,
            "register_callback": lambda self, callback, when=None: None,
        })()
        controller.renderer = FEATHER.FeatherRenderer()
        controller.renderer.send = lambda commands: None
        controller.page = FEATHER.ScreenPage.CONTROL_HOME
        controller.previous_page = FEATHER.ScreenPage.IDLE_HOME
        controller.print_state = FEATHER.PrintState.IDLE
        controller.last_action_time = -1.0
        controller.pending_action = None
        controller.command_depth = 0
        controller.busy_message = None
        controller.toast_until = 0.0
        controller.filament_material = "PLA"
        controller.heating_materials = ("PLA",)
        controller.heating_profiles = {"PLA": (220, 60)}
        controller.cold_pull_materials = ()
        controller.cold_pull_profiles = {}
        controller.temperature_wait = None
        controller._setting = lambda key, default: default
        controller._cancel_delayed_tasks = lambda: None
        controller._require_idle = lambda: None
        controller.feature_manager = LazyFeatureManager(
            controller, FEATHER.FEATURE_SPECS)
        return controller

    def test_calibration_confirm_loads_z_only_when_starting(self):
        controller = self.controller()
        manager = controller.feature_manager

        controller._show_page(FEATHER.ScreenPage.CALIBRATION_HOME)
        common = manager.peek("calibration")
        self.assertIsNotNone(common)
        self.assertIsNone(manager.peek("z"))
        self.assertNotIn("calibration_kind", controller.__dict__)

        controller._dispatch_action("cal.z")
        self.assertEqual(controller.page, FEATHER.ScreenPage.CALIBRATION_CONFIRM)
        self.assertEqual(common.calibration_kind, "z")
        self.assertIsNone(manager.peek("z"))

        import feather_feature_z
        started = []
        with mock.patch.object(
                feather_feature_z.ZCalibrationFeature,
                "start_calibration", lambda feature: started.append(feature)):
            controller.last_action_time = -1.0
            controller._dispatch_action("cal.confirm")

        self.assertEqual(started, [manager.peek("z")])
        self.assertIs(manager.get("z"), manager.peek("z"))

    def test_filament_pages_load_their_declarations_only_when_opened(self):
        controller = self.controller()
        controller.filament_from_pause = False
        controller.extruder = type("Extruder", (), {
            "heater": type("Heater", (), {
                "min_temp": 0, "max_temp": 300})(),
            "min_extrude_temp": 170.0,
            "get_status": lambda self, eventtime: {
                "temperature": 25.0, "target": 0.0},
        })()
        controller.heater_bed = type("Bed", (), {
            "min_temp": 0, "max_temp": 130,
            "get_status": lambda self, eventtime: {
                "temperature": 25.0, "target": 0.0},
        })()
        manager = controller.feature_manager

        self.assertIsNone(manager.peek("filament"))
        controller._show_page(FEATHER.ScreenPage.FILAMENT_MATERIAL)

        self.assertIsNotNone(manager.peek("filament"))
        self.assertEqual(controller.page, FEATHER.ScreenPage.FILAMENT_MATERIAL)

        controller._show_page(FEATHER.ScreenPage.FILAMENT_ACTION)
        self.assertEqual(controller.page, FEATHER.ScreenPage.FILAMENT_ACTION)

    def test_z_motion_pages_arm_abort_only_after_homing(self):
        controller = self.controller()
        status = {"homed_axes": ""}
        controller.toolhead = type("Toolhead", (), {
            "get_status": lambda self, eventtime: status,
        })()
        feature = controller.feature_manager.get("z")
        pages = (
            FEATHER.ScreenPage.Z_OFFSET_PAPER_BRIEFING,
            FEATHER.ScreenPage.Z_OFFSET_PAPER,
            FEATHER.ScreenPage.SAFE_Z_BRIEFING,
            FEATHER.ScreenPage.SAFE_Z_CALIBRATION,
            FEATHER.ScreenPage.LIVE_Z_OFFSET,
        )

        for page in pages:
            self.assertEqual(feature.safety_armed_reasons(page, 1.0), ())

        status["homed_axes"] = "z"
        for page in pages:
            self.assertEqual(
                feature.safety_armed_reasons(page, 2.0), ("z-controls",))

    def test_settings_state_and_modal_flags_live_on_feature(self):
        controller = self.controller()
        controller.params = type("Params", (), {"variables": {}})()
        controller.chamber_light = None

        controller._show_page(FEATHER.ScreenPage.SETTINGS)
        settings = controller.feature_manager.peek("settings")

        self.assertIsNotNone(settings)
        self.assertNotIn("mod_update_pending", controller.__dict__)
        self.assertFalse(controller.feature_manager.input_blocked)
        settings.mod_update_pending = True
        self.assertTrue(controller.feature_manager.input_blocked)
        self.assertTrue(controller.feature_manager.theme_update_blocked)


if __name__ == "__main__":
    unittest.main()
