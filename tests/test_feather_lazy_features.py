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
    FeatureLoadError, FeatureSpec, LazyFeatureManager,
)


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
    'feather_feature_ui_test',
    'feather_feature_calibration', 'feather_feature_z',
    'feather_feature_extruder', 'feather_feature_settings',
    'feather_z_calibration', 'feather_extruder_calibration',
    'feather_mod_settings', 'ff5m_ui.z_offset',
)
assert not [name for name in blocked if name in sys.modules]
assert not [name for name in sys.modules
            if name.startswith('ff5m_ui.z_offset')]
assert 'ff5m_ui.heat.page' not in sys.modules
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
    def register_command(self, name, callback):
        pass

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
    def __init__(self):
        self.printer = Printer()
    def get_printer(self):
        return self.printer
    def getboolean(self, name, default=False):
        return default
    def getfloat(self, name, default=None, minval=None):
        return default
    def get(self, name, default=None):
        return default
    def error(self, message):
        return ValueError(message)

feather_screen.FeatherScreen._start_pre_ready_ui = lambda self: None
controller = feather_screen.FeatherScreen(Config())
assert controller.feature_manager.loaded() == ()
blocked = (
    'feather_feature_ui_test',
    'feather_feature_calibration', 'feather_feature_z',
    'feather_feature_extruder', 'feather_feature_settings',
    'feather_z_calibration', 'feather_extruder_calibration',
    'feather_mod_settings', 'ff5m_ui.z_offset',
)
assert not [name for name in blocked if name in sys.modules]
assert 'ff5m_ui.heat.page' not in sys.modules
""")

    def test_features_load_sequentially_and_are_singletons(self):
        self.run_clean("""
import sys
import feather_screen
from feather_feature_manager import LazyFeatureManager
from ui import FeatherRenderer, Page

class Host:
    pass

host = Host()
host.renderer = FeatherRenderer()
host.renderer.send = lambda commands: None
host._setting = lambda key, default: default
host.feature_manager = LazyFeatureManager(host, feather_screen.FEATURE_SPECS)
manager = host.feature_manager
calibration = manager.get('calibration')
calibration.render(Page.CALIBRATION_HOME)
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
z_feature.render(Page.SAFE_Z_BRIEFING)
pages = [name for name in sys.modules
         if name.startswith('ff5m_ui.z_offset') and name.endswith('.page')]
assert pages == ['ff5m_ui.z_offset.safe_briefing.page'], pages
manager.get('extruder')
assert 'feather_extruder_calibration' in sys.modules
manager.get('settings')
assert 'feather_mod_settings' in sys.modules
""")

    def test_klipper_package_feature_uses_the_core_page_enum(self):
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
    assert feature_module.Page is feather_screen.Page
""")

    def test_klipper_fallback_feature_uses_the_core_page_enum(self):
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
    assert feature_module.Page is feather_screen.Page
"""
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        result = subprocess.run(
            [sys.executable, "-c", source, str(PLUGINS)],
            env=environment, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


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
        controller.page = FEATHER.Page.CONTROL_HOME
        controller.previous_page = FEATHER.Page.IDLE_HOME
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

        controller._show_page(FEATHER.Page.CALIBRATION_HOME)
        common = manager.peek("calibration")
        self.assertIsNotNone(common)
        self.assertIsNone(manager.peek("z"))
        self.assertNotIn("calibration_kind", controller.__dict__)

        controller._dispatch_action("cal.z")
        self.assertEqual(controller.page, FEATHER.Page.CALIBRATION_CONFIRM)
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

    def test_z_motion_pages_arm_abort_only_after_homing(self):
        controller = self.controller()
        status = {"homed_axes": ""}
        controller.toolhead = type("Toolhead", (), {
            "get_status": lambda self, eventtime: status,
        })()
        feature = controller.feature_manager.get("z")
        pages = (
            FEATHER.Page.Z_OFFSET_PAPER_BRIEFING,
            FEATHER.Page.Z_OFFSET_PAPER,
            FEATHER.Page.SAFE_Z_BRIEFING,
            FEATHER.Page.SAFE_Z_CALIBRATION,
            FEATHER.Page.LIVE_Z_OFFSET,
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

        controller._show_page(FEATHER.Page.SETTINGS)
        settings = controller.feature_manager.peek("settings")

        self.assertIsNotNone(settings)
        self.assertNotIn("mod_update_pending", controller.__dict__)
        self.assertFalse(controller.feature_manager.input_blocked)
        settings.mod_update_pending = True
        self.assertTrue(controller.feature_manager.input_blocked)
        self.assertTrue(controller.feature_manager.theme_update_blocked)


if __name__ == "__main__":
    unittest.main()
