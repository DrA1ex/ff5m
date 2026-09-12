"""Version and migration contracts between FF5M and the ui subtree."""

import os
import pathlib
import subprocess
import sys
import unittest


PLUGINS = pathlib.Path(__file__).parents[1] / ".py" / "klipper" / "plugins"
sys.path.insert(0, str(PLUGINS))

import ui  # noqa: E402
from ff5m_ui.keys import AppPage  # noqa: E402
from ff5m_ui.screen import ScreenPage  # noqa: E402
from ff5m_ui.filament.actions import FilamentCommand  # noqa: E402
from ff5m_ui.filament.state import FilamentState  # noqa: E402
from ff5m_ui.heat.actions import HeatCommand  # noqa: E402
from ff5m_ui.heat.state import HeatState  # noqa: E402
from ff5m_ui.move.actions import MoveCommand  # noqa: E402
from ff5m_ui.move.state import MoveState, ToolheadState  # noqa: E402
from ff5m_ui.z_offset.actions import ZOffsetCommand  # noqa: E402
from ff5m_ui.z_offset.paper.state import PaperState  # noqa: E402
from ff5m_ui.z_offset.paper_briefing.state import PaperBriefingState  # noqa: E402
from ff5m_ui.z_offset.safe.state import SafeState  # noqa: E402
from ff5m_ui.z_offset.safe_briefing.state import SafeBriefingState  # noqa: E402
from ff5m_ui.z_offset.summary.state import SummaryState  # noqa: E402


class FrameworkContractTest(unittest.TestCase):
    def test_product_deploys_without_designer_scripts(self):
        self.assertTrue((PLUGINS / "ui" / "__init__.py").is_file())
        self.assertTrue((PLUGINS / "ff5m_ui" / "__init__.py").is_file())

    def test_manifest_is_framework_v2_3(self):
        self.assertEqual(ui.__version__, "2.3.0")
        self.assertEqual(ui.FRAMEWORK_API_VERSION, 2)
        self.assertEqual(ui.REFLECTION_SCHEMA_VERSION, "2.1.0")
        self.assertEqual(ui.framework_manifest(), {
            "name": "feather-ui",
            "version": "2.3.0",
            "api_version": 2,
            "reflection_schema_version": "2.1.0",
            "capabilities": list(ui.FRAMEWORK_CAPABILITIES),
        })

    def test_vendored_framework_contract(self):
        self.assertEqual(ui.__version__, "2.3.0")
        self.assertEqual(ui.FRAMEWORK_API_VERSION, 2)
        self.assertEqual(ui.REFLECTION_SCHEMA_VERSION, "2.1.0")
        self.assertIn("binding-source-authoring", ui.FRAMEWORK_CAPABILITIES)
        for name in (
                "ThemeColor", "ThemeRole", "FeatherRenderer", "PageKey",
                "Action", "StateStore", "DeclarativePage", "Button",
                "ArrowButton", "ToggleSwitch", "EditText", "StateCase",
                "CreationIdentityContract", "RenderReceipt"):
            self.assertTrue(hasattr(ui, name), name)
        self.assertFalse(hasattr(ui, "Page"))
        self.assertFalse(hasattr(ui, "PrintState"))
        self.assertTrue((PLUGINS / "ui" / "themes" / "theme.schema.json").is_file())

    def test_klipper_package_entry_uses_canonical_ui_namespaces(self):
        script = r'''
import importlib
import pathlib
import sys
import types

plugins = pathlib.Path(r"%s")
extras = types.ModuleType("extras")
extras.__package__ = "extras"
extras.__path__ = [str(plugins)]
sys.modules["extras"] = extras

module = importlib.import_module("extras.feather_screen")
assert module.__name__ == "extras.feather_screen"
assert "ui" in sys.modules
assert "ff5m_ui" in sys.modules
assert "extras.ui" not in sys.modules
assert "extras.ff5m_ui" not in sys.modules
assert sys.modules["ui"].__file__.startswith(str(plugins))
assert sys.modules["ff5m_ui"].__file__.startswith(str(plugins))
''' % PLUGINS
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        subprocess.run([sys.executable, "-c", script], check=True,
                       env=environment)

    def test_product_startup_does_not_construct_declarative_pages(self):
        script = (
            "import sys; import feather_screen; "
            "blocked=('ui.layout','ui.components','ui.properties',"
            "'ui.source','ui.reflection'); "
            "pages=[name for name in sys.modules "
            "if name.startswith('ff5m_ui.') and name.endswith('.page')]; "
            "assert not any(name in sys.modules for name in blocked), blocked; "
            "assert not pages, pages")
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(PLUGINS)
        subprocess.run([sys.executable, "-c", script], check=True,
                       env=environment)

    def test_reflection_and_source_hooks_load_only_on_direct_request(self):
        script = r"""
import sys
import ui
import ui.components
assert "ui.reflection" not in sys.modules
assert "ui.source" not in sys.modules
from ui.reflection import reflect_page
assert callable(reflect_page)
assert "ui.source" in sys.modules
"""
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(PLUGINS)
        subprocess.run([sys.executable, "-c", script], check=True,
                       env=environment)

    def test_product_key_wire_namespaces_survive_package_move(self):
        namespaces = {
            AppPage: "ui.pages.keys.AppPage",
            FilamentCommand: "ui.pages.filament.actions.FilamentCommand",
            FilamentState: "ui.pages.filament.state.FilamentState",
            HeatCommand: "ui.pages.heat.actions.HeatCommand",
            HeatState: "ui.pages.heat.state.HeatState",
            MoveCommand: "ui.pages.move.actions.MoveCommand",
            MoveState: "ui.pages.move.state.MoveState",
            ToolheadState: "ui.pages.move.state.ToolheadState",
            ZOffsetCommand: "ui.pages.z_offset.actions.ZOffsetCommand",
            PaperState: "ui.pages.z_offset.paper.state.PaperState",
            PaperBriefingState:
                "ui.pages.z_offset.paper_briefing.state.PaperBriefingState",
            SafeState: "ui.pages.z_offset.safe.state.SafeState",
            SafeBriefingState:
                "ui.pages.z_offset.safe_briefing.state.SafeBriefingState",
            SummaryState: "ui.pages.z_offset.summary.state.SummaryState",
        }
        for key_type, namespace in namespaces.items():
            self.assertEqual(key_type.__key_namespace__, namespace)
            for member in key_type:
                self.assertEqual(ui.serialize_key(member),
                                 "%s.%s" % (namespace, member.name))

    def test_controller_screen_state_is_not_a_declarative_page_key(self):
        self.assertIsInstance(AppPage.HOME, ui.PageKey)
        self.assertNotIsInstance(ScreenPage.IDLE_HOME, ui.PageKey)
        self.assertEqual(
            ui.serialize_key(AppPage.HOME),
            "ui.pages.keys.AppPage.HOME")
        self.assertEqual(
            ui.serialize_key(AppPage.HEAT),
            "ui.pages.keys.AppPage.HEAT")

if __name__ == "__main__":
    unittest.main()
