"""Version and migration contracts between FF5M and the ui subtree."""

import pathlib
import os
import subprocess
import sys
import unittest


PLUGINS = pathlib.Path(__file__).parents[1] / ".py" / "klipper" / "plugins"
sys.path.insert(0, str(PLUGINS))

import ui  # noqa: E402
from ff5m_ui.keys import AppPage  # noqa: E402
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
        root = pathlib.Path(__file__).parents[1]
        self.assertFalse((root / "scripts").exists())
        self.assertTrue((PLUGINS / "ui" / "__init__.py").is_file())
        self.assertTrue((PLUGINS / "ff5m_ui" / "__init__.py").is_file())

    def test_manifest_is_framework_v2(self):
        self.assertEqual(ui.__version__, "2.0.0")
        self.assertEqual(ui.FRAMEWORK_API_VERSION, 2)
        self.assertEqual(ui.REFLECTION_SCHEMA_VERSION, 2)
        self.assertEqual(ui.framework_manifest(), {
            "name": "feather-ui",
            "version": "2.0.0",
            "api_version": 2,
            "reflection_schema_version": 2,
            "capabilities": list(ui.FRAMEWORK_CAPABILITIES),
        })

    def test_product_startup_does_not_construct_declarative_pages(self):
        script = (
            "import sys; import feather_screen; "
            "blocked=('ui.layout','ui.components','ui.properties','ui.source'); "
            "pages=[name for name in sys.modules "
            "if name.startswith('ff5m_ui.') and name.endswith('.page')]; "
            "assert not any(name in sys.modules for name in blocked), blocked; "
            "assert not pages, pages")
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(PLUGINS)
        subprocess.run([sys.executable, "-c", script], check=True,
                       env=environment)

    def test_runtime_source_hooks_defer_designer_only_dependencies(self):
        script = (
            "import sys\n"
            "threading_was_loaded = 'threading' in sys.modules\n"
            "contextlib_was_loaded = 'contextlib' in sys.modules\n"
            "import ui.source as source\n"
            "assert ('threading' in sys.modules) == threading_was_loaded\n"
            "assert ('contextlib' in sys.modules) == contextlib_was_loaded\n"
            "provider = type('Provider', (), {})()\n"
            "assert not source.capture_enabled()\n"
            "with source.source_capture(provider):\n"
            "    assert source.capture_enabled()\n"
            "assert not source.capture_enabled()\n"
            "assert 'threading' in sys.modules\n"
            "assert ('contextlib' in sys.modules) == contextlib_was_loaded\n")
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

    def test_all_navigation_targets_keep_original_page_symbols(self):
        self.assertEqual(
            {ui.serialize_key(page) for page in AppPage},
            {
                "ui.pages.keys.AppPage.HEAT",
                "ui.pages.keys.AppPage.FILAMENT_MATERIAL",
                "ui.pages.keys.AppPage.FILAMENT_ACTION",
                "ui.pages.keys.AppPage.MOVE_STEP",
                "ui.pages.keys.AppPage.MOVE_JOYSTICK",
                "ui.pages.keys.AppPage.Z_OFFSET_SUMMARY",
                "ui.pages.keys.AppPage.Z_OFFSET_PAPER_BRIEFING",
                "ui.pages.keys.AppPage.Z_OFFSET_PAPER",
                "ui.pages.keys.AppPage.SAFE_Z_BRIEFING",
                "ui.pages.keys.AppPage.SAFE_Z_CALIBRATION",
            })

    def test_framework_subtree_has_no_product_or_designer_imports(self):
        framework = PLUGINS / "ui"
        self.assertFalse((framework / "pages").exists())
        for path in framework.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("ff5m_ui", source, str(path))
            self.assertNotIn("feather_preview", source, str(path))


if __name__ == "__main__":
    unittest.main()
