## Tests for the host-side Klipper G-code macro renderer.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import pathlib
import tempfile
import unittest

import jinja2

from tests.gcode_macro_harness import (
    MacroActionError, MacroConfigError, execute_macro_chain, load_macro,
    render_macro)


class GCodeMacroHarnessTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = pathlib.Path(self.directory.name, "macros.cfg")
        self.path.write_text(
            """[gcode_macro EXAMPLE]
variable_count: 2
gcode:
  {% set target = params.TARGET|default(3)|int %}
  {% if printer.toolhead.homed_axes == \"xyz\" %}
    G1 X{target + count}
    {action_respond_info(\"moving\")}
  {% else %}
    {action_raise_error(\"not homed\")}
  {% endif %}
""", encoding="utf-8")

    def test_loads_macro_variables_as_klipper_literals(self):
        macro = load_macro(self.path, "example")

        self.assertEqual(macro.variables, {"count": 2})

    def test_renders_klipper_delimiters_and_records_actions(self):
        result = render_macro(
            self.path, "EXAMPLE",
            printer={"toolhead": {"homed_axes": "xyz"}},
            params={"target": 5})

        self.assertEqual(result.commands, ("G1 X7",))
        self.assertEqual(result.info, ("moving",))

    def test_executes_registered_nested_macros_to_terminal_gcode(self):
        self.path.write_text(
            """[gcode_macro OUTER]
gcode:
  INNER TARGET={params.TARGET}
  M400

[gcode_macro INNER]
gcode:
  G1 X{params.TARGET}
""", encoding="utf-8")

        commands = execute_macro_chain(
            ((self.path, "OUTER"), (self.path, "INNER")),
            "OUTER", params={"TARGET": 7})

        self.assertEqual(commands, ("G1 X7", "M400"))

    def test_nested_macro_cycle_fails_explicitly(self):
        self.path.write_text(
            """[gcode_macro LOOP]
gcode:
  LOOP
""", encoding="utf-8")

        with self.assertRaisesRegex(MacroConfigError, "recursive macro call"):
            execute_macro_chain(((self.path, "LOOP"),), "LOOP")

    def test_strips_comments_before_jinja_evaluation_like_klipper(self):
        self.path.write_text(
            """[gcode_macro COMMENTED]
gcode:
  {% set value = 2  # stripped before Jinja parses this line
      + 3 %}
  G1 X{value} ; stripped before command execution
""", encoding="utf-8")

        result = render_macro(self.path, "COMMENTED")

        self.assertEqual(result.commands, ("G1 X5",))

    def test_action_failure_is_observable(self):
        with self.assertRaisesRegex(MacroActionError, "not homed"):
            render_macro(
                self.path, "EXAMPLE",
                printer={"toolhead": {"homed_axes": ""}})

    def test_invalid_template_fails_at_render(self):
        self.path.write_text(
            "[gcode_macro BAD]\ngcode:\n  {% if broken %}\n",
            encoding="utf-8")

        with self.assertRaises(jinja2.TemplateSyntaxError):
            render_macro(self.path, "BAD")

    def test_missing_macro_fails_explicitly(self):
        with self.assertRaisesRegex(MacroConfigError, "found 0"):
            load_macro(self.path, "MISSING")

    def test_rejects_macro_variables_klipper_cannot_publish_as_json(self):
        self.path.write_text(
            "[gcode_macro BAD]\nvariable_value: {1, 2}\ngcode:\n  G1\n",
            encoding="utf-8")

        with self.assertRaisesRegex(MacroConfigError, "invalid literal"):
            load_macro(self.path, "BAD")

    def test_renders_sections_other_than_gcode_macro(self):
        self.path.write_text(
            """[delayed_gcode BOOT_TASK]
initial_duration: 1
gcode:
  BED_MESH_CLEAR
  BED_MESH_PROFILE LOAD=auto
""", encoding="utf-8")

        result = render_macro(self.path, "BOOT_TASK", section="delayed_gcode")

        self.assertEqual(result.commands, (
            "BED_MESH_CLEAR", "BED_MESH_PROFILE LOAD=auto"))


if __name__ == "__main__":
    unittest.main()
