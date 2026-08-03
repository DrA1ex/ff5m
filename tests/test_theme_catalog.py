## Theme catalog, schema, and refresh lifecycle tests.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import json
import pathlib
import sys
import tempfile
import unittest


PLUGINS = pathlib.Path(__file__).parents[1] / ".py" / "klipper" / "plugins"
sys.path.insert(0, str(PLUGINS))

import ui  # noqa: E402


class ThemeCatalogTest(unittest.TestCase):
    def test_every_bundled_theme_conforms_to_versioned_schema(self):
        directory = pathlib.Path(ui.THEME_DIRECTORY)
        schema = json.loads(
            pathlib.Path(ui.THEME_SCHEMA_PATH).read_text(encoding="utf-8"))
        color_schema = schema["properties"]["colors"]

        self.assertEqual(
            set(ui.FALLBACK_THEME), set(color_schema["required"]))
        self.assertTrue(
            set(ui.OPTIONAL_THEME_ROLE_FALLBACKS).issubset(
                color_schema["properties"]))

        theme_files = sorted(
            path for path in directory.glob("*.json")
            if not path.name.endswith(".schema.json"))
        self.assertTrue(theme_files)
        for path in theme_files:
            with self.subTest(theme=path.name):
                data = json.loads(path.read_text(encoding="utf-8"))
                name, description, colors = ui.validate_theme_data(
                    data, schema=schema)
                self.assertEqual(name, data["name"])
                self.assertTrue(description)
                self.assertTrue(set(ui.FALLBACK_THEME).issubset(colors))

    def test_schema_is_the_runtime_validation_contract(self):
        valid = {
            "schema_version": 1,
            "name": "VALID",
            "description": "Valid test theme",
            "colors": dict(ui.FALLBACK_THEME),
        }
        name, description, colors = ui.validate_theme_data(valid)
        self.assertEqual((name, description), ("VALID", "Valid test theme"))
        self.assertEqual(colors["primary"], ui.COLOR_CYAN)
        self.assertEqual(colors["button_background"], colors["panel"])
        self.assertEqual(colors["button_border"], colors["primary"])
        self.assertEqual(colors["header_text"], colors["primary"])

        invalid_color = dict(valid)
        invalid_color["colors"] = dict(
            valid["colors"], primary="not-a-color")
        with self.assertRaises(ui.ThemeSchemaError):
            ui.validate_theme_data(invalid_color)

        invalid_name = dict(valid, name="MixedCase")
        with self.assertRaises(ui.ThemeSchemaError):
            ui.validate_theme_data(invalid_name)

        invalid_version = dict(valid, schema_version=True)
        with self.assertRaises(ui.ThemeSchemaError):
            ui.validate_theme_data(invalid_version)

        unknown_role = dict(valid)
        unknown_role["colors"] = dict(
            valid["colors"], accidental_role="123abc")
        with self.assertRaises(ui.ThemeSchemaError):
            ui.validate_theme_data(unknown_role)

    def test_theme_file_limit_has_headroom_for_bundled_catalog(self):
        theme_files = [
            path for path in pathlib.Path(ui.THEME_DIRECTORY).glob("*.json")
            if not path.name.endswith(".schema.json")]
        largest = max(path.stat().st_size for path in theme_files)

        self.assertEqual(ui.MAX_THEME_FILE_BYTES, 8 * 1024)
        self.assertLessEqual(largest, ui.MAX_THEME_FILE_BYTES)

    def test_invalid_user_files_are_bounded_logged_and_reported(self):
        with tempfile.TemporaryDirectory() as root:
            root = pathlib.Path(root)
            user = root / "user"
            user.mkdir()

            valid = {
                "schema_version": 1,
                "name": "VALID_USER",
                "description": "Valid user theme",
                "colors": dict(ui.FALLBACK_THEME),
            }
            (user / "valid.json").write_text(
                json.dumps(valid), encoding="utf-8")

            schema_mismatch = dict(valid, name="Broken Theme")
            (user / "schema.json").write_text(
                json.dumps(schema_mismatch), encoding="utf-8")
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
            self.assertEqual(len(catalog.user_issues), 4)
            output = "\n".join(logs.output)
            for filename in issues:
                self.assertIn(filename, output)

            schema_mismatch["name"] = "BROKEN_THEME"
            (user / "schema.json").write_text(
                json.dumps(schema_mismatch), encoding="utf-8")
            with self.assertLogs(level="WARNING"):
                catalog.reload_user_themes()
            self.assertIn("BROKEN_THEME", catalog.names())
            self.assertNotIn(
                "schema.json",
                [issue.filename for issue in catalog.user_issues])

    def test_missing_schema_keeps_fallback_theme_available(self):
        with tempfile.TemporaryDirectory() as directory:
            missing_schema = pathlib.Path(directory, "missing.schema.json")
            catalog = ui.ThemeCatalog(
                bundled_directory=directory, user_directories=(),
                schema_path=str(missing_schema))
            with self.assertLogs(level="WARNING") as logs:
                names = catalog.reload_all()

            self.assertEqual(names, ("DEFAULT",))
            self.assertEqual(
                catalog.palette("DEFAULT")["primary"], ui.COLOR_CYAN)
            self.assertIn("unable to load theme schema",
                          "\n".join(logs.output))

    def test_user_refresh_reuses_bundled_catalog_snapshot(self):
        with tempfile.TemporaryDirectory() as root:
            root = pathlib.Path(root)
            bundled = root / "bundled"
            user = root / "user"
            bundled.mkdir()
            user.mkdir()
            schema_text = pathlib.Path(ui.THEME_SCHEMA_PATH).read_text(
                encoding="utf-8")
            (bundled / "theme.schema.json").write_text(
                schema_text, encoding="utf-8")

            base = {
                "schema_version": 1,
                "name": "BASE",
                "description": "Bundled base",
                "colors": dict(ui.FALLBACK_THEME, primary="111111"),
            }
            (bundled / "base.json").write_text(
                json.dumps(base), encoding="utf-8")
            renderer = ui.FeatherRenderer(
                theme_directories=(str(bundled), str(user)))
            renderer.set_theme("BASE")
            self.assertEqual(renderer.color(ui.COLOR_CYAN), "111111")

            base["colors"]["primary"] = "222222"
            (bundled / "base.json").write_text(
                json.dumps(base), encoding="utf-8")
            custom = {
                "schema_version": 1,
                "name": "CUSTOM",
                "description": "Runtime user theme",
                "colors": dict(ui.FALLBACK_THEME, primary="abcdef"),
            }
            (user / "custom.json").write_text(
                json.dumps(custom), encoding="utf-8")

            renderer.reload_user_themes()
            self.assertIn("CUSTOM", renderer.theme_names())
            renderer.set_theme("BASE")
            self.assertEqual(renderer.color(ui.COLOR_CYAN), "111111")
            renderer.set_theme("CUSTOM")
            self.assertEqual(renderer.color(ui.COLOR_CYAN), "abcdef")
            sent = []
            renderer.send = sent.append
            renderer.footer(20, 200, 25, 60, "LAN", "IDLE")
            sent.clear()
            custom["colors"]["primary"] = "fedcba"
            (user / "custom.json").write_text(
                json.dumps(custom), encoding="utf-8")
            renderer.reload_user_themes()
            self.assertEqual(renderer.color(ui.COLOR_CYAN), "fedcba")
            redrawn = "\n".join(renderer.begin_page("Themes"))
            self.assertIn("-c fedcba", redrawn)

            renderer.reload_themes()
            renderer.set_theme("BASE")
            self.assertEqual(renderer.color(ui.COLOR_CYAN), "222222")


if __name__ == "__main__":
    unittest.main()
