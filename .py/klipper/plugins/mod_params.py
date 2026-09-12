## Mod's parameters management
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license


import ast, configparser, logging, threading
import json

from dataclasses import dataclass
from enum import Enum
from typing import List, Any, Dict, Type, Union, Optional

DEFAULT_BOOL_OPTIONS = ["NO", "YES"]


@dataclass
class DeprecationParameter:
    key: str
    new_key: str
    mapping: Dict[str, str]
    # A pure rename keeps the stored value; the mapping then translates only
    # the values that must change, such as a retired default.
    carry_over: bool = False


@dataclass
class Parameter:
    key: str
    type: Type
    default: Any
    label: str
    description: Optional[str] = None
    options: Union[List[str], Dict[Any, str], None] = None
    readonly: bool = False
    hidden: bool = False
    order: int = 0
    warning: Optional[str] = None
    deprecated: Optional[DeprecationParameter] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    fraction_digits: Optional[int] = None
    restart: Optional[str] = None
    ui_inverted: bool = False
    ui_category: Optional[str] = None
    ui_visible_if: Optional[Dict[str, Any]] = None
    ui_order: Optional[int] = None


@dataclass(frozen=True)
class ParameterCategory:
    id: str
    label: str
    order: int = 0


class ModParamManagement:
    def __init__(self, config):
        self.loaded = False

        self.config = config
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object("gcode")

        self.declaration = self.config.get("declaration")
        self.filename = self.config.get("filename")
        self.variables = dict()
        # Both G-code and local UIs use this manager.  Keep their read-modify-
        # write sequences serialized so a full variables-file write cannot
        # discard a value committed by another caller.
        self._variables_lock = threading.RLock()

        self.reactor = self.printer.get_reactor()
        gcode_macro = self.printer.load_object(config, "gcode_macro")
        self.changes_gcode_present = config.get("changes_gcode", None) is not None
        if self.changes_gcode_present:
            self.changes_template = gcode_macro.load_template(config, "changes_gcode")

        self.params: List[Parameter] = list()
        self.params_map: Dict[str, Parameter] = dict()
        self.migration_map: Dict[str, DeprecationParameter] = dict()
        self.type_mapping: Dict[str, Type] = dict()

        self._load_declaration()
        self._reload()

        self.gcode.register_command("LIST_MOD_PARAMS", self.cmd_LIST_MOD_PARAMS)
        self.gcode.register_command("RELOAD_MOD_PARAMS", self.cmd_RELOAD_MOD_PARAMS)

        self.gcode.register_command("GET_MOD_PARAM", self.cmd_GET_MOD_PARAM)
        self.gcode.register_command("SET_MOD_PARAM", self.cmd_SET_MOD_PARAM)

        self.gcode.register_command("GET_MOD", self.cmd_GET_MOD)
        self.gcode.register_command("SET_MOD", self.cmd_SET_MOD)

    def _run_gcode(self, *cmds: str):
        self.gcode.run_script_from_command("\n".join(cmds))

    def _load_declaration(self):
        try:
            with open(self.declaration, 'r', encoding="utf-8") as file:
                data = json.load(file)
        except:
            msg = "Unable to load declaration file."
            logging.exception(msg)
            raise self.printer.command_error(msg)

        self.type_mapping = {
            "bool": bool,
            "int": int,
            "float": float,
            "str": str
        }

        declaration_ui = data.get("ui", {})
        if not isinstance(declaration_ui, dict):
            raise ValueError("[mod_params]: Invalid declaration UI metadata!")

        categories = []
        category_ids = set()
        category_by_parameter = {}
        ui_order_by_parameter = {}
        fallback_category_id = None

        for category_data in declaration_ui.get("categories", []):
            if not isinstance(category_data, dict):
                raise ValueError("[mod_params]: Invalid UI category metadata!")

            category = ParameterCategory(
                id=str(category_data["id"]),
                label=str(category_data["label"]),
                order=int(category_data.get("order", 0)))
            if category.id in category_ids:
                raise ValueError('[mod_params]: Duplicate UI category "%s"!' % category.id)
            category_ids.add(category.id)
            categories.append(category)

            fallback = category_data.get("fallback", False)
            if not isinstance(fallback, bool):
                raise ValueError('[mod_params]: UI category "%s" has invalid fallback!' % category.id)
            if fallback:
                if fallback_category_id is not None:
                    raise ValueError("[mod_params]: Multiple fallback UI categories!")
                fallback_category_id = category.id

            category_parameters = category_data.get("parameters", [])
            if not isinstance(category_parameters, list):
                raise ValueError('[mod_params]: UI category "%s" has invalid parameters!' % category.id)
            for ui_order, parameter_key in enumerate(category_parameters):
                if (not isinstance(parameter_key, str)
                        or parameter_key in category_by_parameter):
                    raise ValueError('[mod_params]: Invalid or duplicate categorized parameter!')
                category_by_parameter[parameter_key] = category.id
                ui_order_by_parameter[parameter_key] = ui_order

        self.ui_categories = sorted(categories, key=lambda category: (category.order, category.id))
        self.ui_categories_map = dict((category.id, category) for category in self.ui_categories)
        category_order = dict((category.id, index) for index, category in enumerate(self.ui_categories))

        dependency_by_parameter = {}
        for dependency in declaration_ui.get("strict_visibility_dependencies", []):
            if (not isinstance(dependency, dict)
                    or not isinstance(dependency.get("parameter"), str)
                    or not isinstance(dependency.get("depends_on"), str)
                    or dependency.get("operator") != "equals"
                    or "value" not in dependency
                    or dependency["parameter"] in dependency_by_parameter):
                raise ValueError("[mod_params]: Invalid UI visibility dependency!")
            dependency_by_parameter[dependency["parameter"]] = {
                "parameter": dependency["depends_on"],
                "operator": dependency["operator"],
                "value": dependency["value"],
            }

        for enum_name, enum_data in data.get("enums", {}).items():
            if enum_name in self.type_mapping:
                logging.error(f'[mod_params]: Type "{enum_name}" already exists!')
                continue

            new_enum = self._create_enum_from_json(enum_name, enum_data)
            self.type_mapping[enum_name] = new_enum

        params = []
        for param_data in sorted(data["parameters"], key=lambda p: [p.get("order", 0), p.get("label", "")]):
            param_type = self.type_mapping.get(param_data['type'])
            if not param_type:
                logging.error(f'[mod_params]: Parameter "{param_data["key"]}" has wrong type "{param_data["type"]}"!')
                continue

            # ui.inverted is display metadata for boolean controls only.  It
            # must never transform values loaded, saved, or exposed to macros.
            ui_data = param_data.get("ui", {})
            if not isinstance(ui_data, dict):
                raise ValueError(f'[mod_params]: Parameter "{param_data["key"]}" has invalid UI metadata!')
            ui_inverted = ui_data.get("inverted", False)
            if not isinstance(ui_inverted, bool):
                raise ValueError(f'[mod_params]: Parameter "{param_data["key"]}" has non-boolean ui.inverted!')
            if "inverted" in ui_data and param_type is not bool:
                raise ValueError(f'[mod_params]: Parameter "{param_data["key"]}" uses ui.inverted but is not boolean!')

            ui_category = ui_data.get("category", category_by_parameter.get(param_data["key"], fallback_category_id))
            if ui_category is not None:
                if not isinstance(ui_category, str):
                    raise ValueError(f'[mod_params]: Parameter "{param_data["key"]}" has invalid ui.category!')
                if ui_category not in self.ui_categories_map:
                    raise ValueError(f'[mod_params]: Parameter "{param_data["key"]}" uses unknown ui.category!')

            ui_visible_if = ui_data.get("visible_if", dependency_by_parameter.get(param_data["key"]))
            if ui_visible_if is not None:
                if (not isinstance(ui_visible_if, dict)
                        or not isinstance(ui_visible_if.get("parameter"), str)
                        or ui_visible_if.get("operator") != "equals"
                        or "value" not in ui_visible_if):
                    raise ValueError(f'[mod_params]: Parameter "{param_data["key"]}" has invalid ui.visible_if!')
                ui_visible_if = dict(ui_visible_if)

            if issubclass(param_type, Enum):
                param_data["default"] = param_type[param_data["default"]].name

            deprecated = param_data.get("deprecated")
            if deprecated is not None:
                if not isinstance(deprecated, dict):
                    raise ValueError(f'[mod_params]: Parameter "{param_data["key"]}" has invalid deprecated metadata!')
                if not isinstance(deprecated.get("mapping", {}), dict):
                    raise ValueError(f'[mod_params]: Parameter "{param_data["key"]}" has invalid deprecated.mapping!')
                if not isinstance(deprecated.get("carry_over", False), bool):
                    raise ValueError(f'[mod_params]: Parameter "{param_data["key"]}" has invalid deprecated.carry_over!')

            listed_category = category_by_parameter.get(param_data["key"])
            ui_order = (ui_order_by_parameter.get(param_data["key"])
                        if ui_category == listed_category else None)

            param = Parameter(
                key=param_data["key"],
                type=param_type,
                default=param_data["default"],
                label=param_data["label"],
                description=param_data.get("description"),
                options=param_data.get("options"),
                readonly=param_data.get("readonly", False),
                hidden=param_data.get("hidden", False),
                order=param_data.get("order", 0),
                warning=param_data.get("warning", None),
                minimum=param_data.get("minimum"),
                maximum=param_data.get("maximum"),
                fraction_digits=param_data.get("fraction_digits"),
                restart=param_data.get("restart"),
                ui_inverted=ui_inverted,
                ui_category=ui_category,
                ui_visible_if=ui_visible_if,
                ui_order=ui_order,
                deprecated=DeprecationParameter(
                    key=deprecated["key"],
                    new_key=param_data["key"],
                    mapping=deprecated.get("mapping", {}),
                    carry_over=deprecated.get("carry_over", False),
                ) if deprecated is not None else None
            )

            if param_type == bool and param.options is None:
                param.options = DEFAULT_BOOL_OPTIONS

            params.append(param)

        # Explicit category lists define manual UI order. Other category members
        # follow them by legacy order/key; the fallback category uses order/key entirely.
        def ui_sort_key(param):
            category_index = category_order.get(param.ui_category, len(category_order))
            if param.ui_category != fallback_category_id and param.ui_order is not None:
                return category_index, 0, param.ui_order, ""
            return category_index, 1, param.order, param.key

        params.sort(key=ui_sort_key)

        self.params = params
        self.params_map = {p.key: p for p in params}
        self.migration_map = {p.deprecated.key: p.deprecated for p in params if p.deprecated}

    def _create_enum_from_json(self, enum_name: str, enum_data: Dict[str, Any]) -> Type[Enum]:
        try:
            return Enum(enum_name, enum_data["values"])
        except:
            msg = f'Unable to build enum {enum_name} from declaration file.'
            logging.exception(msg)
            raise self.printer.command_error(msg)

    def _reload(self):
        result = dict()
        parser = configparser.ConfigParser()

        try:
            parser.read(self.filename)
            if not parser.has_section("Variables"):
                parser.add_section("Variables")

            parsed = dict()
            for key, value in parser.items("Variables"):
                if key in self.params_map:
                    parsed[key] = ast.literal_eval(value)
                elif key in self.migration_map:
                    migration = self.migration_map[key]

                    if migration.new_key in parsed:
                        logging.info(f'[mod_params]: Ignoring deprecated "{key}"; "{migration.new_key}" is already set.')
                        continue

                    literal = migration.mapping.get(value, value if migration.carry_over else None)
                    if literal is not None:
                        parsed[migration.new_key] = ast.literal_eval(literal)
                        logging.info(f'[mod_params]: Migrated "{key}" -> "{migration.new_key}": {parsed[migration.new_key]}')
                    else:
                        logging.error(f'[mod_params]: Unable to migrate deprecated parameter: "{key}"')
                else:
                    logging.error(f'[mod_params]: Read unknown parameter while parsing: "{key}"')

            for param in self.params:
                key = param.key
                value = parsed.get(key)

                try:
                    result[key] = self._load_param(param, value)
                except:
                    logging.error(f'[mod_params]: Unable to parse {key} value: "{value}"; Expected type: {param.type}')
                    result[key] = self._load_param(param, param.default)

        except Exception:
            msg = "[mod_params] Unable to parse variable file."
            logging.exception(msg)
            raise self.printer.command_error(msg)

        self.variables = result

    def _load_param(self, param: Parameter, value: Optional[str]):
        if issubclass(param.type, Enum):
            # Defaults and persisted enum values both use member names.
            name = value if value is not None else param.default
            return param.type[name.strip()].value

        if param.type == bool:
            return param.type(int(value)) if value is not None else param.default

        return param.type(value) if value is not None else param.default

    def _transform(self, param: Parameter, value: Optional[Any]):
        if issubclass(param.type, Enum):
            return param.type(value).name if value is not None else param.default

        if param.type == bool:
            return int(value if value is not None else param.default)

        return value if value is not None else param.default

    def _save_all(self):
        parser = configparser.ConfigParser()
        parser.add_section("Variables")

        for param in self.params:
            value = self.variables.get(param.key)
            value_to_save = self._transform(param, value)
            parser.set("Variables", param.key, repr(value_to_save))

        try:
            with open(self.filename, "w") as f:
                parser.write(f)
        except:
            msg = "Unable to save variable"
            logging.exception(msg)
            raise self.gcode.error(msg)

    def set_value(self, key: str, value: Any, force: bool = False):
        """Set a parameter without passing user text through the G-code parser.

        Feather uses this path for its on-screen editors so arbitrary string
        values cannot break quoting or turn into additional G-code commands.
        Change hooks retain the same reactor scheduling as SET_MOD.
        """
        if key not in self.params_map:
            raise ValueError('Unknown parameter: "%s"' % key)

        param = self.params_map[key]
        if param.readonly and not force:
            raise ValueError('Updating readonly parameter "%s" is forbidden.' % key)

        try:
            if param.type == str:
                new_value = str(value)
            else:
                new_value = self._load_param(param, str(value))
        except Exception:
            raise ValueError('Failed to update parameter "%s"' % key)

        self._store_value(param, new_value)

        lock = getattr(self, "_variables_lock", None)
        with lock:
            return self._transform(param, self.variables[key])

    def _store_value(self, param: Parameter, new_value: Any):
        """Atomically update one value and persist the complete snapshot."""
        lock = getattr(self, "_variables_lock", None)
        if lock is None:
            lock = self._variables_lock = threading.RLock()

        changed = False
        with lock:
            previous_value = self.variables[param.key]
            if new_value != previous_value:
                self.variables[param.key] = new_value
                try:
                    self._save_all()
                except Exception:
                    self.variables[param.key] = previous_value
                    raise
                changed = True

        if changed:
            self.reactor.register_callback(
                lambda _, p=param: self._notify_committed_change(p))

        return changed

    def _format_label(self, param: Parameter, value: Any):
        if param.options:
            return f'{param.label}: {param.options[value]}'

        return f'{param.label}: {value}'

    def _print_param(self, gcmd, param: Parameter):
        value = self._transform(param, self.variables[param.key])
        gcmd.respond_raw(self._format_label(param, value))
        if issubclass(param.type, Enum):
            gcmd.respond_raw(f'  // {[value.name for value in param.type]}')
        if not param.readonly:
            gcmd.respond_raw(f'  --> SET_MOD PARAM="{param.key}" VALUE={repr(value)}')

    def cmd_LIST_MOD_PARAMS(self, gcmd):
        for param in self.params:
            if param.hidden: continue

            self._print_param(gcmd, param)

    def cmd_RELOAD_MOD_PARAMS(self, _):
        previous = dict(self.variables)
        self._reload()
        if self.variables != previous:
            self.reactor.register_callback(
                lambda _: self._emit_changed_event())

    def cmd_GET_MOD_PARAM(self, gcmd):
        key = gcmd.get('PARAM')

        if key in self.migration_map:
            new_key = self.migration_map[key].new_key
            raise gcmd.error(f"!! Parameter {key!r} is deprecated. Use {new_key!r} instead!")
        elif key not in self.params_map:
            raise gcmd.error(f'Unknown parameter: "{key}"')

        param = self.params_map[key]
        self._print_param(gcmd, param)
        self._print_warning(param)

    def cmd_GET_MOD(self, gcmd):
        self.cmd_GET_MOD_PARAM(gcmd)

    def cmd_SET_MOD_PARAM(self, gcmd):
        key = gcmd.get('PARAM')
        value = gcmd.get('VALUE')
        force = int(gcmd.get('FORCE', 0))

        if key in self.migration_map:
            new_key = self.migration_map[key].new_key
            raise gcmd.error(f"!! Parameter {key!r} is deprecated. Use {new_key!r} instead!")
        elif key not in self.params_map:
            similar_key = self._find_similar_param(key, list(self.params_map.keys()))
            if similar_key:
                gcmd.respond_raw(f"!! Unknown parameter: {key!r}")
                gcmd.respond_info("Did you mean this?")
                gcmd.respond_info(f"SET_MOD PARAM={similar_key!r} VALUE={value!r}")
                return
            else:
                raise gcmd.error(f'Unknown parameter: "{key}"')

        param = self.params_map[key]
        if param.readonly and not force:
            raise gcmd.error(f'Updating readonly parameter "{key}" is forbidden.')

        try:
            new_value = self._load_param(param, value)
        except:
            raise gcmd.error(f'Failed to update parameter "{key}" with value: "{value}"')

        self._store_value(param, new_value)

        if not param.hidden:
            transformed = self._transform(param, self.variables[key])
            gcmd.respond_raw("SET: " + self._format_label(param, transformed))

        self._print_warning(param)

    def cmd_SET_MOD(self, gcmd):
        self.cmd_SET_MOD_PARAM(gcmd)

    def _print_warning(self, param):
        if param.warning:
            for text in param.warning.split("\n"):
                self.gcode.respond_raw(text.strip())

    def _notify_changed(self, param: Parameter):
        context = self.changes_template.create_template_context()

        value = self.variables[param.key]
        context["changes"] = {
            "key": param.key,
            "value": self._transform(param, value),
            "raw": value,
        }

        template = self.changes_template.render(context)

        try:
            self.gcode.run_script(template)
        except:
            logging.exception(f"mod_params: Script running error:\n{template}")

    def _emit_changed_event(self):
        try:
            self.printer.send_event("mod_params:changed")
        except Exception:
            logging.exception("mod_params: changed event handler failed")

    def _notify_committed_change(self, param: Parameter):
        self._emit_changed_event()
        if self.changes_gcode_present:
            self._notify_changed(param)

    def get_status(self, _):
        return {'variables': self.variables}

    @staticmethod
    def _levenshtein_distance(s1, s2):
        if len(s1) < len(s2):
            return ModParamManagement._levenshtein_distance(s2, s1)

        if len(s2) == 0:
            return len(s1)

        previous_row = list(range(len(s2) + 1))
        for i, c1 in enumerate(s1):
            current_row = [i + 1]
            for j, c2 in enumerate(s2):
                insertions = previous_row[j + 1] + 1
                deletions = current_row[j] + 1
                substitutions = previous_row[j] + (c1 != c2)
                current_row.append(min(insertions, deletions, substitutions))
            previous_row = current_row

        return previous_row[-1]

    @staticmethod
    def _find_similar_param(misspelled, param_list):
        if not param_list: return None

        distances = [(param, ModParamManagement._levenshtein_distance(misspelled, param)) for param in param_list]
        min_distance = min(distances, key=lambda x: x[1])[1]

        if min_distance <= 10:
            closest_params = [param for param, dist in distances if dist == min_distance]
            return closest_params[0]

        return None


def load_config(config):
    return ModParamManagement(config)
