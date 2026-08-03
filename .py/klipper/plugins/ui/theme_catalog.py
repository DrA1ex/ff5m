## Theme loading, validation, and catalog lifecycle for Feather UI.
##
## This module owns the theme file contract and filesystem policy. Renderers
## consume normalized palettes and do not need to know how JSON files are
## discovered, validated, or overridden.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import json
import logging
import os
import re
import stat
from collections import namedtuple

from .theme import resolve_theme


DEFAULT_THEME = "DEFAULT"
THEME_NAME_ALIASES = {
    "CYBERPANK_RED": "CYBERPUNK_RED",
    "CYBERPANK_YELLOW": "CYBERPUNK_YELLOW",
}
THEME_DIRECTORY = os.path.normpath(os.path.join(
    os.path.dirname(os.path.realpath(__file__)), "themes"))
USER_THEME_DIRECTORY = "/opt/config/mod_data/themes"
THEME_SCHEMA_PATH = os.path.join(THEME_DIRECTORY, "theme.schema.json")
FALLBACK_THEME_DESCRIPTION = "cyan Forge-X palette"

# The largest bundled theme is currently under 1 KiB. Five times that
# size is still below 5 KiB, so round up to the next power of two.
# This provides comfortable headroom for future expansion while
# keeping the maximum file size strictly bounded.
MAX_THEME_FILE_BYTES = 8 * 1024

ThemeFileIssue = namedtuple(
    "ThemeFileIssue", ("name", "description", "filename"))

# This palette is the final safety net when the bundled catalog cannot be read.
# Keep it independent from default.json so Feather can still draw an interface
# after a damaged or incomplete installation.
FALLBACK_THEME = {
    "background": "030607", "panel": "050c0f", "primary": "35d9e6",
    "primary_dark": "244c66", "secondary": "b47aff",
    "secondary_dark": "872187", "warning": "f2c94c", "danger": "ff4d5a",
    "danger_background": "120708", "text": "d9e4e8", "bright": "ffffff",
    "dim": "56656c", "border": "295c66", "muted": "263238",
    "success": "56c596", "pressed_background": "103238",
    "overlay": "010203",
}


class ThemeSchemaError(ValueError):
    """Raised when a theme document does not satisfy theme.schema.json."""


class ThemeFileTooLarge(ValueError):
    """Raised before parsing a theme that exceeds the hard size limit."""


_SCHEMA_CACHE = {}


def _load_schema(path=THEME_SCHEMA_PATH):
    normalized = os.path.realpath(path)
    cached = _SCHEMA_CACHE.get(normalized)
    if cached is not None:
        return cached
    with open(normalized, "r", encoding="utf-8") as stream:
        schema = json.load(stream)
    if not isinstance(schema, dict):
        raise ThemeSchemaError("theme schema root must be an object")
    _SCHEMA_CACHE[normalized] = schema
    return schema


def _resolve_ref(root, reference):
    if not reference.startswith("#/"):
        raise ThemeSchemaError("unsupported schema reference %r" % reference)
    value = root
    for token in reference[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if not isinstance(value, dict) or token not in value:
            raise ThemeSchemaError("unknown schema reference %r" % reference)
        value = value[token]
    return value


def _json_equal(left, right):
    # Python considers True == 1, while JSON Schema treats booleans and
    # numbers as different JSON values. Numeric int/float values remain
    # equivalent, matching JSON Schema's mathematical number comparison.
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    return left == right


def _matches_type(value, expected):
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    raise ThemeSchemaError("unsupported schema type %r" % expected)


def _validate_json_schema(value, schema, root, path="$"):
    """Validate the JSON Schema subset used by theme.schema.json.

    Forge-X runtime intentionally depends only on Python's standard library.
    The validator implements the small Draft 2020-12 subset used by the
    versioned theme schema, making that JSON file the executable contract
    without adding a third-party package to Klipper.
    """
    if "$ref" in schema:
        target = _resolve_ref(root, schema["$ref"])
        return _validate_json_schema(value, target, root, path)

    if "oneOf" in schema:
        matches = 0
        for candidate in schema["oneOf"]:
            try:
                _validate_json_schema(value, candidate, root, path)
            except ThemeSchemaError:
                continue
            matches += 1
        if matches != 1:
            raise ThemeSchemaError(
                "%s must match exactly one allowed schema" % path)

    if "enum" in schema and not any(
            _json_equal(value, item) for item in schema["enum"]):
        raise ThemeSchemaError(
            "%s must be one of %r" % (path, schema["enum"]))

    if "const" in schema and not _json_equal(value, schema["const"]):
        raise ThemeSchemaError(
            "%s must equal %r" % (path, schema["const"]))

    expected_type = schema.get("type")
    if expected_type is not None and not _matches_type(value, expected_type):
        raise ThemeSchemaError("%s must be a %s" % (path, expected_type))

    if isinstance(value, dict):
        required = schema.get("required", ())
        missing = [key for key in required if key not in value]
        if missing:
            raise ThemeSchemaError(
                "%s is missing required properties: %s" %
                (path, ", ".join(missing)))
        properties = schema.get("properties", {})
        for key, item in value.items():
            child_schema = properties.get(key)
            if child_schema is None:
                if schema.get("additionalProperties", True) is False:
                    raise ThemeSchemaError(
                        "%s.%s is not an allowed property" % (path, key))
                continue
            _validate_json_schema(
                item, child_schema, root, "%s.%s" % (path, key))

    if isinstance(value, str):
        minimum = schema.get("minLength")
        maximum = schema.get("maxLength")
        if minimum is not None and len(value) < minimum:
            raise ThemeSchemaError(
                "%s must contain at least %d characters" % (path, minimum))
        if maximum is not None and len(value) > maximum:
            raise ThemeSchemaError(
                "%s must contain at most %d characters" % (path, maximum))
        pattern = schema.get("pattern")
        if pattern is not None and re.search(pattern, value) is None:
            raise ThemeSchemaError(
                "%s does not match pattern %s" % (path, pattern))



def validate_theme_data(data, schema=None, schema_path=THEME_SCHEMA_PATH):
    """Return a normalized ``(name, description, resolved_theme)`` tuple."""
    active_schema = schema if schema is not None else _load_schema(schema_path)
    _validate_json_schema(data, active_schema, active_schema)
    name = data["name"]
    description = data["description"].strip()
    colors = dict(
        (name, value.strip().lower())
        for name, value in data["colors"].items())
    roles = dict(
        (name, value.strip().lower())
        for name, value in data.get("roles", {}).items())
    return name, description, resolve_theme(colors, roles)


def normalize_theme_name(name):
    normalized = str(name or DEFAULT_THEME).strip().upper()
    normalized = normalized.replace("-", "_").replace(" ", "_")
    return THEME_NAME_ALIASES.get(normalized, normalized)


def _read_theme_document(path):
    metadata = os.stat(path)
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("theme path is not a regular file")
    if metadata.st_size > MAX_THEME_FILE_BYTES:
        raise ThemeFileTooLarge(
            "file exceeds %d-byte limit" % MAX_THEME_FILE_BYTES)
    with open(path, "rb") as stream:
        payload = stream.read(MAX_THEME_FILE_BYTES + 1)
    if len(payload) > MAX_THEME_FILE_BYTES:
        raise ThemeFileTooLarge(
            "file exceeds %d-byte limit" % MAX_THEME_FILE_BYTES)
    return json.loads(payload.decode("utf-8"))


def _theme_issue_name(filename, data=None):
    candidate = data.get("name") if isinstance(data, dict) else None
    if not isinstance(candidate, str) or not candidate.strip():
        candidate = os.path.splitext(filename)[0]
    # Keep the diagnostic row single-line even when an invalid file contains
    # control characters in its attempted name.
    return " ".join(str(candidate).split()) or "UNNAMED"


def _theme_issue_description(exc):
    if isinstance(exc, ThemeFileTooLarge):
        return "FILE TOO LARGE"
    if isinstance(exc, UnicodeDecodeError):
        return "INVALID FILE"
    if isinstance(exc, json.JSONDecodeError):
        return "INVALID JSON"
    if isinstance(exc, ThemeSchemaError):
        return "SCHEMA MISMATCH"
    if isinstance(exc, OSError):
        return "READ ERROR"
    return "INVALID FILE"


class ThemeCatalog:
    """Load bundled themes once and refresh user themes independently."""

    def __init__(self, bundled_directory=THEME_DIRECTORY,
                 user_directories=(USER_THEME_DIRECTORY,),
                 schema_path=THEME_SCHEMA_PATH):
        self.bundled_directory = bundled_directory
        self.user_directories = tuple(user_directories)
        self.schema_path = schema_path
        self._schema = None
        self._bundled_themes = {}
        self._bundled_descriptions = {}
        self._user_themes = {}
        self._user_descriptions = {}
        self._user_issues = ()
        self._themes = {}
        self._descriptions = {}
        self._reset_to_fallback()

    @classmethod
    def from_directories(cls, directories):
        directories = tuple(directories)
        bundled = directories[0] if directories else THEME_DIRECTORY
        users = directories[1:] if len(directories) > 1 else ()
        schema_path = os.path.join(bundled, "theme.schema.json")
        if not os.path.isfile(schema_path):
            schema_path = THEME_SCHEMA_PATH
        return cls(bundled, users, schema_path)

    @property
    def themes(self):
        return dict(self._themes)

    @property
    def descriptions(self):
        return dict(self._descriptions)

    @property
    def user_issues(self):
        return tuple(self._user_issues)

    def names(self):
        return tuple(self._themes)

    def description(self, name):
        return self._descriptions.get(normalize_theme_name(name), "")

    def palette(self, name):
        return self._themes.get(normalize_theme_name(name))

    def reload_all(self):
        """Reload bundled and user themes, used at construction and ready."""
        self._load_active_schema()
        fallback = resolve_theme(FALLBACK_THEME)
        self._bundled_themes = {DEFAULT_THEME: fallback}
        self._bundled_descriptions = {
            DEFAULT_THEME: FALLBACK_THEME_DESCRIPTION}
        themes, descriptions, _issues = self._load_directory(
            self.bundled_directory)
        self._bundled_themes.update(themes)
        self._bundled_descriptions.update(descriptions)
        self._reload_user_sources()
        self._compose()
        return self.names()

    def reload_user_themes(self):
        """Refresh only writable user directories and reuse bundled cache."""
        if self._schema is None:
            self._load_active_schema()
        self._reload_user_sources()
        self._compose()
        return self.names()

    def ensure_user_directories(self):
        for directory in self.user_directories:
            try:
                os.makedirs(directory, exist_ok=True)
            except OSError as exc:
                logging.warning(
                    "[feather_screen] unable to create theme directory %s: %s",
                    directory, exc)

    def _reset_to_fallback(self):
        fallback = resolve_theme(FALLBACK_THEME)
        self._bundled_themes = {DEFAULT_THEME: fallback}
        self._bundled_descriptions = {
            DEFAULT_THEME: FALLBACK_THEME_DESCRIPTION}
        self._user_themes = {}
        self._user_descriptions = {}
        self._user_issues = ()
        self._compose()

    def _load_active_schema(self):
        try:
            self._schema = _load_schema(self.schema_path)
        except Exception as exc:
            self._schema = None
            logging.warning(
                "[feather_screen] unable to load theme schema %s: %s",
                self.schema_path, exc)

    def _reload_user_sources(self):
        self._user_themes = {}
        self._user_descriptions = {}
        issues = []
        for directory in self.user_directories:
            themes, descriptions, directory_issues = self._load_directory(
                directory, collect_issues=True)
            self._user_themes.update(themes)
            self._user_descriptions.update(descriptions)
            issues.extend(directory_issues)
        self._user_issues = tuple(issues)

    def _compose(self):
        self._themes = dict(self._bundled_themes)
        self._descriptions = dict(self._bundled_descriptions)
        self._themes.update(self._user_themes)
        self._descriptions.update(self._user_descriptions)

    def _load_directory(self, directory, collect_issues=False):
        themes = {}
        descriptions = {}
        issues = []
        if self._schema is None or not os.path.isdir(directory):
            return themes, descriptions, issues
        for filename in sorted(os.listdir(directory)):
            lowered = filename.lower()
            if (not lowered.endswith(".json")
                    or lowered.endswith(".schema.json")):
                continue
            path = os.path.join(directory, filename)
            data = None
            try:
                data = _read_theme_document(path)
                name, description, theme = validate_theme_data(
                    data, schema=self._schema)
                themes[name] = theme
                descriptions[name] = description
            except Exception as exc:
                logging.warning(
                    "[feather_screen] invalid theme %s: %s", path, exc)
                if collect_issues:
                    issues.append(ThemeFileIssue(
                        _theme_issue_name(filename, data),
                        _theme_issue_description(exc), filename))
        return themes, descriptions, issues
