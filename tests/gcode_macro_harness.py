## Test-only Klipper G-code macro renderer.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import ast
import dataclasses
import json
import pathlib
import re

import jinja2


class MacroConfigError(ValueError):
    pass


class MacroActionError(RuntimeError):
    pass


@dataclasses.dataclass(frozen=True)
class MacroSource:
    name: str
    gcode: str
    variables: dict


@dataclasses.dataclass(frozen=True)
class RenderedMacro:
    text: str
    commands: tuple
    info: tuple
    remote_calls: tuple


_SECTION = re.compile(r"^\s*\[([^]]+)\]\s*(?:[#;].*)?$")


def load_macro(path, name):
    sections = _read_sections(path)
    section_name = "gcode_macro %s" % name
    matches = [options for current, options in sections
               if current.casefold() == section_name.casefold()]
    if len(matches) != 1:
        raise MacroConfigError(
            "expected one [%s] section in %s; found %d"
            % (section_name, path, len(matches)))

    options = matches[0]
    if "gcode" not in options:
        raise MacroConfigError("[%s] has no gcode option" % section_name)

    variables = {}
    for option, value in options.items():
        if not option.startswith("variable_"):
            continue
        try:
            parsed = ast.literal_eval(value)
            json.dumps(parsed, separators=(",", ":"))
            variables[option[len("variable_"):]] = parsed
        except (SyntaxError, TypeError, ValueError) as error:
            raise MacroConfigError(
                "invalid literal for %s in [%s]: %s"
                % (option, section_name, error)) from error
    return MacroSource(name, options["gcode"], variables)


def render_macro(path, name, *, printer=None, params=None, rawparams="",
                 variables=None):
    macro = load_macro(path, name)
    context = dict(macro.variables)
    if variables:
        context.update(variables)

    info = []
    remote_calls = []

    def action_respond_info(message):
        info.append(str(message))
        return ""

    def action_raise_error(message):
        raise MacroActionError(str(message))

    def action_emergency_stop(message="action_emergency_stop"):
        raise MacroActionError(str(message))

    def action_call_remote_method(method, **kwargs):
        remote_calls.append((method, kwargs))
        return ""

    context.update({
        "printer": printer or {},
        "params": {str(key).upper(): str(value)
                   for key, value in (params or {}).items()},
        "rawparams": rawparams,
        "action_respond_info": action_respond_info,
        "action_raise_error": action_raise_error,
        "action_emergency_stop": action_emergency_stop,
        "action_call_remote_method": action_call_remote_method,
    })
    environment = jinja2.Environment("{%", "%}", "{", "}")
    text = environment.from_string(macro.gcode).render(context)
    commands = tuple(
        line.strip() for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#"))
    return RenderedMacro(text, commands, tuple(info), tuple(remote_calls))


def _read_sections(path):
    path = pathlib.Path(path)
    sections = []
    current = None
    current_option = None

    for line_number, raw_line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1):
        raw_line = _strip_config_comments(raw_line)
        match = _SECTION.match(raw_line)
        if match:
            current = (match.group(1), {})
            sections.append(current)
            current_option = None
            continue
        if current is None or not raw_line.strip():
            continue
        if raw_line[0].isspace():
            if current_option is not None:
                options = current[1]
                options[current_option] += "\n" + raw_line.lstrip()
            continue
        if raw_line.lstrip().startswith(("#", ";")):
            continue
        if ":" not in raw_line:
            raise MacroConfigError(
                "%s:%d: expected an option" % (path, line_number))
        option, value = raw_line.split(":", 1)
        current_option = option.strip().lower()
        current[1][current_option] = value.strip()
    return sections


def _strip_config_comments(line):
    line = line.split("#", 1)[0]
    for position, character in enumerate(line):
        if character == ";" and (position == 0 or line[position - 1].isspace()):
            return line[:position].rstrip()
    return line.rstrip()
