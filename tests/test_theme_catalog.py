## Theme resolution, schema, and catalog lifecycle tests.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import json
import pathlib
import re
import sys
import tempfile
import unittest


PLUGINS = pathlib.Path(__file__).parents[1] / ".py" / "klipper" / "plugins"
sys.path.insert(0, str(PLUGINS))

import ui  # noqa: E402


_HEX = re.compile(r"^[0-9a-f]{6}$")


def _base_colors(**overrides):
    colors = dict(ui.FALLBACK_THEME)
    colors.update(overrides)
    return colors


def _theme(name="VALID", colors=None, roles=None):
    value = {
        "schema_version": 2,
        "name": name,
        "description": "Theme used by a behavioral test",
        "colors": dict(colors or _base_colors()),
    }
    if roles is not None:
        value["roles"] = dict(roles)
    return value


class ThemeResolutionTest(unittest.TestCase):
    def test_missing_product_roles_use_conservative_base_accent(self):
        resolved = ui.resolve_theme(_base_colors())
        primary = resolved.resolve(ui.ThemeColor.PRIMARY)

        self.assertEqual(
            resolved.resolve(ui.ThemeRole.TEMPERATURE_NOZZLE), primary)
        self.assertEqual(
            resolved.resolve(ui.ThemeRole.TEMPERATURE_BED), primary)
        self.assertEqual(
            resolved.resolve(ui.ThemeRole.TEMPERATURE_FAN), primary)

    def test_role_can_follow_base_color_or_override_it_physically(self):
        colors = _base_colors(primary="101010", secondary="202020")
        resolved = ui.resolve_theme(colors, {
            "temperature_nozzle": "secondary",
            "temperature_bed": "abcdef",
        })

        self.assertEqual(
            resolved.resolve(ui.ThemeRole.TEMPERATURE_NOZZLE),
            resolved.resolve(ui.ThemeColor.SECONDARY))
        self.assertEqual(
            resolved.resolve(ui.ThemeRole.TEMPERATURE_BED), "abcdef")
        self.assertEqual(
            resolved.resolve(ui.ThemeRole.TEMPERATURE_FAN),
            resolved.resolve(ui.ThemeColor.PRIMARY))

    def test_resolved_theme_is_a_snapshot_not_a_live_view(self):
        colors = _base_colors(primary="111111")
        roles = {"temperature_nozzle": "primary"}
        resolved = ui.resolve_theme(colors, roles)
        colors["primary"] = "222222"
        roles["temperature_nozzle"] = "333333"

        self.assertEqual(
            resolved.resolve(ui.ThemeColor.PRIMARY), "111111")
        self.assertEqual(
            resolved.resolve(ui.ThemeRole.TEMPERATURE_NOZZLE), "111111")

    def test_role_to_role_reference_is_rejected(self):
        with self.assertRaises(ValueError):
            ui.resolve_theme(_base_colors(), {
                "temperature_bed": "temperature_nozzle",
            })

    def test_button_accent_changes_visual_identity_without_changing_state(self):
        renderer = ui.FeatherRenderer()
        renderer._palette = ui.resolve_theme(_base_colors(), {
            "temperature_bed": "abcdef",
        })

        enabled = "\n".join(renderer.button(
            "bed", 0, 0, 80, 40, "BED", state="enabled",
            accent=ui.ThemeRole.TEMPERATURE_BED))
        disabled = "\n".join(renderer.button(
            "bed.disabled", 0, 0, 80, 40, "BED", state="disabled",
            accent=ui.ThemeRole.TEMPERATURE_BED))

        self.assertIn("--border abcdef", enabled)
        self.assertIn("--text-color abcdef", enabled)
        self.assertIn("--id ", enabled)
        self.assertNotIn("abcdef", disabled)
        self.assertNotIn("--id ", disabled)

    def test_renderer_requires_typed_tokens(self):
        renderer = ui.FeatherRenderer()
        self.assertRegex(renderer.color(ui.ThemeColor.PRIMARY), _HEX)
        self.assertRegex(
            renderer.color(ui.ThemeRole.TEMPERATURE_BED), _HEX)
        with self.assertRaises(TypeError):
            renderer.color("primary")
        with self.assertRaises(TypeError):
            renderer.color("35d9e6")


class ThemeCatalogTest(unittest.TestCase):
    def test_every_bundled_theme_produces_a_complete_resolved_palette(self):
        directory = pathlib.Path(ui.THEME_DIRECTORY)
        schema = json.loads(
            pathlib.Path(ui.THEME_SCHEMA_PATH).read_text(encoding="utf-8"))
        theme_files = sorted(
            path for path in directory.glob("*.json")
            if not path.name.endswith(".schema.json"))
        self.assertTrue(theme_files)

        for path in theme_files:
            with self.subTest(theme=path.name):
                data = json.loads(path.read_text(encoding="utf-8"))
                name, description, resolved = ui.validate_theme_data(
                    data, schema=schema)
                self.assertEqual(name, data["name"])
                self.assertTrue(description)
                for token in tuple(ui.ThemeColor) + tuple(ui.ThemeRole):
                    self.assertRegex(resolved.resolve(token), _HEX)

    def test_schema_enforces_new_contract_without_legacy_layout(self):
        ui.validate_theme_data(_theme(roles={
            "temperature_nozzle": "secondary",
            "temperature_bed": "abcdef",
        }))

        invalid_documents = []
        invalid_documents.append(dict(_theme(), schema_version=1))

        legacy_role = _theme()
        legacy_role["colors"]["header_text"] = "abcdef"
        invalid_documents.append(legacy_role)

        role_to_role = _theme(roles={
            "temperature_bed": "temperature_nozzle",
        })
        invalid_documents.append(role_to_role)

        unknown_role = _theme(roles={"temperature_chamber": "primary"})
        invalid_documents.append(unknown_role)

        for document in invalid_documents:
            with self.subTest(document=document):
                with self.assertRaises(ui.ThemeSchemaError):
                    ui.validate_theme_data(document)

    def test_theme_file_limit_has_headroom_for_bundled_catalog(self):
        theme_files = [
            path for path in pathlib.Path(ui.THEME_DIRECTORY).glob("*.json")
            if not path.name.endswith(".schema.json")]
        largest = max(path.stat().st_size for path in theme_files)

        self.assertGreater(ui.MAX_THEME_FILE_BYTES, 0)
        self.assertLessEqual(largest, ui.MAX_THEME_FILE_BYTES)

    def test_invalid_user_files_are_bounded_logged_and_reported(self):
        with tempfile.TemporaryDirectory() as root:
            root = pathlib.Path(root)
            user = root / "user"
            user.mkdir()
            (user / "valid.json").write_text(
                json.dumps(_theme("VALID_USER")), encoding="utf-8")
            (user / "schema.json").write_text(
                json.dumps(_theme("Broken Theme")), encoding="utf-8")
            (user / "syntax.json").write_text("{broken", encoding="utf-8")
            (user / "binary.json").write_bytes(b"\x00\xff\x10")
            (user / "large.json").write_bytes(
                b" " * (ui.MAX_THEME_FILE_BYTES + 1))

            catalog = ui.ThemeCatalog(
                bundled_directory=ui.THEME_DIRECTORY,
                user_directories=(str(user),),
                schema_path=ui.THEME_SCHEMA_PATH)
            with self.assertLogs(level="WARNING") as logs:
                names = catalog.reload_all()

            self.assertIn("VALID_USER", names)
            issues = dict(
                (issue.filename, (issue.name, issue.description))
                for issue in catalog.user_issues)
            self.assertEqual(issues["schema.json"],
                             ("Broken Theme", "SCHEMA MISMATCH"))
            self.assertEqual(issues["syntax.json"],
                             ("syntax", "INVALID JSON"))
            self.assertEqual(issues["binary.json"],
                             ("binary", "INVALID FILE"))
            self.assertEqual(issues["large.json"],
                             ("large", "FILE TOO LARGE"))
            output = "\n".join(logs.output)
            for filename in issues:
                self.assertIn(filename, output)

    def test_missing_schema_keeps_a_drawable_fallback_theme(self):
        with tempfile.TemporaryDirectory() as directory:
            missing_schema = pathlib.Path(directory, "missing.schema.json")
            catalog = ui.ThemeCatalog(
                bundled_directory=directory, user_directories=(),
                schema_path=str(missing_schema))
            with self.assertLogs(level="WARNING") as logs:
                names = catalog.reload_all()

            self.assertEqual(names, ("DEFAULT",))
            fallback = catalog.palette("DEFAULT")
            self.assertRegex(fallback.resolve(ui.ThemeColor.PRIMARY), _HEX)
            self.assertRegex(
                fallback.resolve(ui.ThemeRole.TEMPERATURE_BED), _HEX)
            self.assertIn("unable to load theme schema",
                          "\n".join(logs.output))

    def test_user_refresh_reuses_bundled_snapshot_and_reloads_user_theme(self):
        with tempfile.TemporaryDirectory() as root:
            root = pathlib.Path(root)
            bundled = root / "bundled"
            user = root / "user"
            bundled.mkdir()
            user.mkdir()
            (bundled / "theme.schema.json").write_text(
                pathlib.Path(ui.THEME_SCHEMA_PATH).read_text(encoding="utf-8"),
                encoding="utf-8")

            base = _theme("BASE", _base_colors(primary="111111"))
            (bundled / "base.json").write_text(
                json.dumps(base), encoding="utf-8")
            renderer = ui.FeatherRenderer(
                theme_directories=(str(bundled), str(user)))
            renderer.set_theme("BASE")
            self.assertEqual(
                renderer.color(ui.ThemeColor.PRIMARY), "111111")

            base["colors"]["primary"] = "222222"
            (bundled / "base.json").write_text(
                json.dumps(base), encoding="utf-8")
            custom = _theme(
                "CUSTOM", _base_colors(primary="abcdef"),
                roles={"temperature_bed": "fedcba"})
            (user / "custom.json").write_text(
                json.dumps(custom), encoding="utf-8")

            renderer.reload_user_themes()
            renderer.set_theme("BASE")
            self.assertEqual(
                renderer.color(ui.ThemeColor.PRIMARY), "111111")
            renderer.set_theme("CUSTOM")
            self.assertEqual(
                renderer.color(ui.ThemeColor.PRIMARY), "abcdef")
            self.assertEqual(
                renderer.color(ui.ThemeRole.TEMPERATURE_BED), "fedcba")

            custom["colors"]["primary"] = "123456"
            custom["roles"]["temperature_bed"] = "primary"
            (user / "custom.json").write_text(
                json.dumps(custom), encoding="utf-8")
            renderer.reload_user_themes()
            self.assertEqual(
                renderer.color(ui.ThemeColor.PRIMARY), "123456")
            self.assertEqual(
                renderer.color(ui.ThemeRole.TEMPERATURE_BED), "123456")

            renderer.reload_themes()
            renderer.set_theme("BASE")
            self.assertEqual(
                renderer.color(ui.ThemeColor.PRIMARY), "222222")

    def test_role_override_changes_actual_rendered_output(self):
        with tempfile.TemporaryDirectory() as root:
            root = pathlib.Path(root)
            bundled = root / "bundled"
            bundled.mkdir()
            (bundled / "theme.schema.json").write_text(
                pathlib.Path(ui.THEME_SCHEMA_PATH).read_text(encoding="utf-8"),
                encoding="utf-8")
            document = _theme(
                "CUSTOM", roles={"temperature_bed": "abcdef"})
            (bundled / "custom.json").write_text(
                json.dumps(document), encoding="utf-8")

            renderer = ui.FeatherRenderer(theme_directories=(str(bundled),))
            renderer.set_theme("CUSTOM")
            command = renderer.text(
                10, 10, "BED", ui.ThemeRole.TEMPERATURE_BED)
            self.assertIn("-c abcdef", command)


if __name__ == "__main__":
    unittest.main()
