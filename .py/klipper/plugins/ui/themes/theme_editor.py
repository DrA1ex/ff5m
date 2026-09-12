#!/usr/bin/env python3
## Small zero-dependency local web editor for Feather UI themes.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

"""Small zero-dependency local web editor for Feather UI themes.

Typical zero-config use when this file itself lives in the themes directory:

    cd themes
    python3 feather_theme_editor_20260817_1532.py

The editor:
- discovers theme JSON files in the current directory;
- finds theme.schema.json next to them;
- searches upward for theme.py / framework/ui/theme.py;
- serves on 127.0.0.1 by default, with configurable bind and URL hosts;
- opens the browser unless --no-open is supplied;
- generates light or dark palettes from common color harmonies;
- keeps generated themes in local browser storage;
- edits ThemeColor and ThemeRole values live;
- validates, downloads, and optionally applies UI plus splash/logged themes.

No mandatory third-party dependencies are required. If jsonschema is installed,
theme.schema.json is additionally used for validation.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


HEX_RE = re.compile(r"^[0-9a-fA-F]{6}$")
THEME_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,31}$")
MAX_THEME_FILE_BYTES = 8 * 1024
DEFAULT_WWW_DIR = Path(__file__).with_name("www")
DEFAULT_USER_THEMES_DIR = Path("/opt/config/mod_data/themes")
DEFAULT_PRINTER_MARKER = Path("/opt/config/mod/mod_params.json")
DEFAULT_MOONRAKER_URL = "http://127.0.0.1:7125"

STATIC_ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/theme-editor.css": ("theme-editor.css", "text/css; charset=utf-8"),
    "/theme-editor.js": ("theme-editor.js", "text/javascript; charset=utf-8"),
}

FALLBACK_COLORS = (
    "background", "panel", "primary", "primary_dark", "secondary",
    "secondary_dark", "warning", "danger", "danger_background", "text",
    "bright", "dim", "border", "muted", "success", "pressed_background",
    "overlay",
)

FALLBACK_ROLES = (
    "button_background", "button_border", "button_text",
    "button_selected_background", "button_selected_border",
    "button_selected_text", "accent_background", "accent_border",
    "accent_text", "header_background", "header_text",
    "header_border", "temperature_nozzle", "temperature_bed",
    "temperature_fan",
)

FALLBACK_DEFAULTS = {
    "button_background": "panel",
    "button_border": "primary",
    "button_text": "primary",
    "button_selected_background": "panel",
    "button_selected_border": "secondary",
    "button_selected_text": "secondary",
    "accent_background": "primary_dark",
    "accent_border": "primary",
    "accent_text": "bright",
    "header_background": "panel",
    "header_text": "primary",
    "header_border": "border",
    "temperature_nozzle": "primary",
    "temperature_bed": "primary",
    "temperature_fan": "primary",
}

SCREEN_THEME_COLOR_SOURCES = (
    ("background", "background"),
    ("text", "primary"),
    ("text_highlight", "bright"),
    ("accent", "secondary"),
    ("wire", "text"),
    ("shadow", "primary_dark"),
    ("beam", "bright"),
    ("glitch_primary", "primary"),
    ("glitch_secondary", "secondary"),
    ("glitch_cut", "overlay"),
    ("subtitle", "text"),
    ("debug", "dim"),
    ("info", "text"),
    ("warn", "warning"),
    ("error", "danger"),
    ("uptime", "secondary"),
)


def normalize_hex(value):
    value = str(value).strip().lower().lstrip("#")
    if HEX_RE.fullmatch(value) is None:
        raise ValueError("expected six-digit HEX")
    return value


def parse_contract_source(path):
    source = Path(path).read_text(encoding="utf-8")

    color_match = re.search(
        r"class\s+ThemeColor\s*\([^)]*\)\s*:(.*?)(?=\nclass\s|\Z)",
        source, re.S)
    colors = tuple(re.findall(
        r'^\s+[A-Z][A-Z0-9_]*\s*=\s*"([^"]+)"',
        color_match.group(1), re.M)) if color_match else ()

    role_match = re.search(
        r"class\s+ThemeRole\s*\([^)]*\)\s*:(.*?)(?=\n(?:class|DEFAULT_THEME_ROLES)\b|\Z)",
        source, re.S)
    if role_match:
        role_body = role_match.group(1)
    else:
        # Also tolerate an in-progress source where the class name was lost but
        # the enum members and DEFAULT_THEME_ROLES are still present.
        anchor = source.find('BUTTON_BACKGROUND = "button_background"')
        if anchor >= 0:
            start = source.rfind("class ", 0, anchor)
            end = source.find("DEFAULT_THEME_ROLES", anchor)
            role_body = source[start:end if end >= 0 else None]
        else:
            role_body = ""

    roles = tuple(re.findall(
        r'^\s+[A-Z][A-Z0-9_]*\s*=\s*"([^"]+)"',
        role_body, re.M))

    defaults = {}
    if color_match and role_body:
        for role_symbol, color_symbol in re.findall(
                r"ThemeRole\.([A-Z][A-Z0-9_]*)\s*:\s*ThemeColor\.([A-Z][A-Z0-9_]*)",
                source):
            role_name = re.search(
                r'^\s*%s\s*=\s*"([^"]+)"' % re.escape(role_symbol),
                role_body, re.M)
            color_name = re.search(
                r'^\s*%s\s*=\s*"([^"]+)"' % re.escape(color_symbol),
                color_match.group(1), re.M)
            if role_name and color_name:
                defaults[role_name.group(1)] = color_name.group(1)

    if not colors or not roles or any(role not in defaults for role in roles):
        raise RuntimeError("could not recover complete theme contract")

    return {
        "colors": colors,
        "roles": roles,
        "defaults": defaults,
        "mode": "parsed-source",
    }


def load_contract(theme_py):
    if theme_py is None or not Path(theme_py).is_file():
        return {
            "colors": FALLBACK_COLORS,
            "roles": FALLBACK_ROLES,
            "defaults": dict(FALLBACK_DEFAULTS),
            "mode": "embedded",
        }

    path = Path(theme_py)
    try:
        spec = importlib.util.spec_from_file_location(
            "feather_theme_editor_contract", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return {
            "colors": tuple(item.value for item in module.ThemeColor),
            "roles": tuple(item.value for item in module.ThemeRole),
            "defaults": {
                role.value: module.DEFAULT_THEME_ROLES[role].value
                for role in module.ThemeRole
            },
            "mode": "imported",
        }
    except Exception:
        try:
            return parse_contract_source(path)
        except Exception:
            return {
                "colors": FALLBACK_COLORS,
                "roles": FALLBACK_ROLES,
                "defaults": dict(FALLBACK_DEFAULTS),
                "mode": "embedded-fallback",
            }


def directory_has_themes(path):
    if not path.is_dir():
        return False

    for candidate in path.glob("*.json"):
        if candidate.name == "theme.schema.json":
            continue
        try:
            document = json.loads(candidate.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(document, dict) and isinstance(document.get("colors"), dict):
            return True

    return False


def discover_themes_dir(base, explicit):
    if explicit is not None:
        path = Path(explicit).expanduser().resolve()
        if not path.is_dir():
            raise SystemExit("--themes-dir is not a directory: %s" % path)
        return path

    if directory_has_themes(base):
        return base

    child = base / "themes"
    if directory_has_themes(child):
        return child

    return base


def search_roots(base, max_parents=6):
    roots = [base]
    current = base
    for _index in range(max_parents):
        parent = current.parent
        if parent == current:
            break
        roots.append(parent)
        current = parent
    return roots


def discover_theme_py(base):
    for root in search_roots(base):
        for candidate in (
                root / "theme.py",
                root / "framework" / "ui" / "theme.py",
                root / "src" / "theme.py"):
            if candidate.is_file():
                return candidate
    return None


def discover_schema(base, themes_dir):
    direct = themes_dir / "theme.schema.json"
    if direct.is_file():
        return direct

    for root in search_roots(base):
        for candidate in (
                root / "theme.schema.json",
                root / "themes" / "theme.schema.json"):
            if candidate.is_file():
                return candidate
    return None


def load_theme_files(themes_dir):
    result = []
    for path in sorted(themes_dir.glob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(document, dict) and isinstance(document.get("colors"), dict):
            result.append({
                "file": path.name,
                "name": str(document.get("name") or path.stem),
                "description": str(document.get("description") or ""),
            })
    return result


def validate_basic(document, contract):
    errors = []
    if not isinstance(document, dict):
        return ["Theme must be a JSON object."]

    name = document.get("name")
    if not isinstance(name, str) or not name:
        errors.append("name is required")
    elif THEME_NAME_RE.fullmatch(name) is None:
        errors.append(
            "name must start with A-Z and contain only A-Z, 0-9, or _ "
            "(32 characters maximum)")

    description = document.get("description")
    if not isinstance(description, str) or not description:
        errors.append("description is required")
    elif len(description) > 80:
        errors.append("description must be 80 characters or fewer")
    elif re.search(r"\S", description) is None:
        errors.append("description must contain non-whitespace text")

    colors = document.get("colors")
    if not isinstance(colors, dict):
        errors.append("colors must be an object")
        colors = {}

    expected_colors = set(contract["colors"])
    actual_colors = set(colors)
    for item in sorted(expected_colors - actual_colors):
        errors.append("missing color: %s" % item)
    for item in sorted(actual_colors - expected_colors):
        errors.append("unknown color: %s" % item)

    for key, value in colors.items():
        try:
            normalize_hex(value)
        except Exception:
            errors.append("%s must be six-digit HEX" % key)

    roles = document.get("roles") or {}
    if not isinstance(roles, dict):
        errors.append("roles must be an object")
        roles = {}

    expected_roles = set(contract["roles"])
    color_names = set(contract["colors"])
    for item in sorted(set(roles) - expected_roles):
        errors.append("unknown role: %s" % item)

    for key, value in roles.items():
        raw = str(value).strip().lower().lstrip("#")
        if raw not in color_names and HEX_RE.fullmatch(raw) is None:
            errors.append(
                "%s must reference ThemeColor or contain six-digit HEX" % key)

    return errors


def schema_validate(document, schema_path):
    if schema_path is None:
        return [], False

    try:
        import jsonschema
    except Exception:
        return [], False

    try:
        schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
        jsonschema.validate(document, schema)
        return [], True
    except Exception as exc:
        return ["schema: %s" % exc], True


def safe_filename(name):
    value = str(name).strip().lower()
    value = re.sub(r"[^a-z0-9._-]+", "-", value)
    value = value.strip("-._")
    return value or "custom-theme"


def screen_theme_document(document):
    colors = document["colors"]
    return {
        "schema_version": 1,
        "name": document["name"],
        "description": document["description"],
        "colors": {
            screen_role: normalize_hex(colors[theme_color])
            for screen_role, theme_color in SCREEN_THEME_COLOR_SOURCES
        },
    }


class ThemeApplyError(RuntimeError):
    def __init__(self, message, status=HTTPStatus.CONFLICT, saved=False):
        super().__init__(message)
        self.status = status
        self.saved = saved


def moonraker_error_message(value, fallback):
    if not isinstance(value, dict):
        return fallback
    error = value.get("error")
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])
    return fallback


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="wb", dir=str(path.parent), prefix=".theme-editor-",
                delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(path))
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


class App:
    def __init__(self, themes_dir, theme_py, schema, www_dir=DEFAULT_WWW_DIR,
                 user_themes_dir=DEFAULT_USER_THEMES_DIR,
                 printer_marker=DEFAULT_PRINTER_MARKER,
                 moonraker_url=DEFAULT_MOONRAKER_URL, requester=None):
        self.themes_dir = Path(themes_dir)
        self.theme_py = Path(theme_py) if theme_py else None
        self.schema = Path(schema) if schema else None
        self.www_dir = Path(www_dir)
        self.user_themes_dir = Path(user_themes_dir)
        self.screen_themes_dir = self.user_themes_dir / "splash"
        self.printer_marker = Path(printer_marker)
        self.moonraker_url = str(moonraker_url).rstrip("/")
        self.requester = requester or urllib.request.urlopen
        self.contract = load_contract(self.theme_py)

    def theme_list(self):
        return load_theme_files(self.themes_dir)

    def safe_theme_path(self, filename):
        filename = Path(str(filename)).name
        path = (self.themes_dir / filename).resolve()
        if path.parent != self.themes_dir.resolve():
            raise ValueError("invalid theme path")
        if not path.is_file():
            raise FileNotFoundError(filename)
        return path

    def load_theme(self, filename):
        document = json.loads(self.safe_theme_path(filename).read_text(encoding="utf-8"))
        if not isinstance(document, dict) or "colors" not in document:
            raise ValueError("not a theme document")
        return document

    def validate(self, document):
        errors = validate_basic(document, self.contract)
        schema_used = False
        if not errors:
            schema_errors, schema_used = schema_validate(document, self.schema)
            errors.extend(schema_errors)
        return errors, schema_used

    def static_asset(self, request_path):
        asset = STATIC_ASSETS.get(request_path)
        if asset is None:
            raise FileNotFoundError(request_path)
        filename, content_type = asset
        return (self.www_dir / filename).read_bytes(), content_type

    def moonraker_json(self, method, path, payload=None):
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.moonraker_url + path, data=data, headers=headers,
            method=method)
        try:
            response = self.requester(request, timeout=2.0)
            with response:
                value = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                value = json.loads(exc.read().decode("utf-8"))
            except Exception:
                value = None
            raise ThemeApplyError(
                moonraker_error_message(value, "Moonraker rejected the request"),
                HTTPStatus.CONFLICT) from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise ThemeApplyError(
                "Printer API is unavailable: %s" % exc,
                HTTPStatus.SERVICE_UNAVAILABLE) from exc
        if not isinstance(value, dict) or value.get("error"):
            raise ThemeApplyError(
                moonraker_error_message(value, "Printer API returned an error"),
                HTTPStatus.CONFLICT)
        return value

    def runtime_status(self):
        if not self.printer_marker.is_file():
            return {
                "printer": False,
                "can_apply": False,
                "reason": "Apply is available only when the editor runs on the printer.",
            }
        try:
            info = self.moonraker_json("GET", "/printer/info")
            result = info.get("result")
            state = result.get("state") if isinstance(result, dict) else None
            if str(state or "").lower() != "ready":
                return {
                    "printer": True,
                    "can_apply": False,
                    "reason": "Klipper is not ready%s." % (
                        ": " + str(state) if state else ""),
                }
            value = self.moonraker_json(
                "GET", "/printer/objects/query?print_stats=state"
                "&virtual_sdcard=is_active")
            result = value.get("result")
            status = result.get("status") if isinstance(result, dict) else None
            if not isinstance(status, dict):
                raise ThemeApplyError("Printer status is incomplete")
            print_stats = status.get("print_stats")
            virtual_sdcard = status.get("virtual_sdcard")
            if not isinstance(print_stats, dict) or not isinstance(
                    virtual_sdcard, dict):
                raise ThemeApplyError("Printer status is incomplete")
            print_state = str(print_stats.get("state") or "").lower()
            print_active = (print_state in ("printing", "paused")
                            or bool(virtual_sdcard.get("is_active")))
            if print_active:
                return {
                    "printer": True,
                    "can_apply": False,
                    "print_state": print_state,
                    "reason": "A theme cannot be applied while printing or paused.",
                }
            return {
                "printer": True,
                "can_apply": True,
                "print_state": print_state,
                "reason": "",
            }
        except ThemeApplyError as exc:
            return {
                "printer": True,
                "can_apply": False,
                "reason": str(exc),
            }

    @staticmethod
    def applied_filename(identity):
        identity = str(identity or "").strip()
        if not identity or len(identity) > 200:
            raise ThemeApplyError(
                "Theme identity is missing or invalid.",
                HTTPStatus.BAD_REQUEST)
        slug = safe_filename(identity).replace(".", "-")[:40]
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:10]
        return "theme-editor-%s-%s.json" % (slug, digest)

    def apply_theme(self, value):
        if not isinstance(value, dict):
            raise ThemeApplyError("Apply request must be an object.",
                                  HTTPStatus.BAD_REQUEST)
        document = value.get("document")
        errors, _schema_used = self.validate(document)
        if errors:
            raise ThemeApplyError(" • ".join(errors), HTTPStatus.BAD_REQUEST)

        runtime = self.runtime_status()
        if not runtime["can_apply"]:
            raise ThemeApplyError(runtime["reason"])

        filename = self.applied_filename(value.get("identity"))
        data = (json.dumps(document, ensure_ascii=False, indent=2)
                + "\n").encode("utf-8")
        if len(data) > MAX_THEME_FILE_BYTES:
            raise ThemeApplyError(
                "Theme exceeds the %d-byte runtime limit." %
                MAX_THEME_FILE_BYTES, HTTPStatus.BAD_REQUEST)
        path = self.user_themes_dir / filename

        screen_document = screen_theme_document(document)
        screen_data = (json.dumps(screen_document, ensure_ascii=False, indent=2)
                       + "\n").encode("utf-8")
        screen_path = (self.screen_themes_dir /
                       (safe_filename(document["name"]) + ".json"))
        try:
            previous_screen_data = screen_path.read_bytes()
        except FileNotFoundError:
            previous_screen_data = None
        except OSError as exc:
            raise ThemeApplyError(
                "Unable to read the existing splash/logged theme: %s" % exc,
                HTTPStatus.INTERNAL_SERVER_ERROR) from exc

        screen_written = False
        try:
            atomic_write(screen_path, screen_data)
            screen_written = True
            atomic_write(path, data)
        except OSError as exc:
            if screen_written:
                try:
                    if previous_screen_data is None:
                        screen_path.unlink(missing_ok=True)
                    else:
                        atomic_write(screen_path, previous_screen_data)
                except OSError as rollback_exc:
                    raise ThemeApplyError(
                        "Unable to save the theme pair; restoring the prior "
                        "splash/logged theme also failed: %s" % rollback_exc,
                        HTTPStatus.INTERNAL_SERVER_ERROR) from exc
            raise ThemeApplyError(
                "Unable to save the theme pair on the printer: %s" % exc,
                HTTPStatus.INTERNAL_SERVER_ERROR) from exc

        command = "_APPLY_TM_EDITOR_THEME THEME='%s'" % document["name"]
        try:
            self.moonraker_json(
                "POST", "/printer/gcode/script", {"script": command})
        except ThemeApplyError as exc:
            raise ThemeApplyError(
                "Theme was saved but could not be applied: %s" % exc,
                exc.status, saved=True) from exc
        return {
            "ok": True,
            "name": document["name"],
            "filename": filename,
            "screen_filename": screen_path.name,
        }


def make_handler(app, log_requests=True):
    class Handler(BaseHTTPRequestHandler):
        server_version = "FeatherThemeEditor/1.3"

        def log_message(self, fmt, *args):
            if log_requests:
                print("[http] " + fmt % args)

        def send_bytes(self, data, content_type, status=HTTPStatus.OK, headers=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(data)

        def send_json(self, value, status=HTTPStatus.OK, headers=None):
            data = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_bytes(data, "application/json; charset=utf-8", status, headers)

        def read_json(self):
            length = int(self.headers.get("Content-Length") or "0")
            if length > 2_000_000:
                raise ValueError("request too large")
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path in STATIC_ASSETS:
                try:
                    data, content_type = app.static_asset(parsed.path)
                    self.send_bytes(data, content_type)
                except OSError as exc:
                    self.send_json(
                        {"error": "web asset unavailable: %s" % exc},
                        HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            if parsed.path == "/api/config":
                self.send_json({
                    "themes": app.theme_list(),
                    "colors": list(app.contract["colors"]),
                    "roles": list(app.contract["roles"]),
                    "defaults": app.contract["defaults"],
                    "contract_mode": app.contract["mode"],
                    "schema": app.schema.name if app.schema else None,
                })
                return
            if parsed.path == "/api/runtime":
                self.send_json(app.runtime_status())
                return
            if parsed.path == "/api/theme":
                filename = (urllib.parse.parse_qs(parsed.query).get("file") or [""])[0]
                try:
                    self.send_json(app.load_theme(filename))
                except FileNotFoundError:
                    self.send_json({"error": "theme not found"}, HTTPStatus.NOT_FOUND)
                except Exception as exc:
                    self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

        def do_POST(self):
            try:
                document = self.read_json()
            except Exception as exc:
                self.send_json({"ok": False, "errors": [str(exc)]}, HTTPStatus.BAD_REQUEST)
                return

            if self.path == "/api/validate":
                errors, schema_used = app.validate(document)
                self.send_json({"ok": not errors, "errors": errors, "schema_used": schema_used})
                return

            if self.path == "/api/export":
                errors, _schema_used = app.validate(document)
                if errors:
                    self.send_json({"ok": False, "errors": errors}, HTTPStatus.BAD_REQUEST)
                    return
                filename = safe_filename(document.get("name")) + ".json"
                data = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
                self.send_bytes(
                    data,
                    "application/json; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="%s"' % filename},
                )
                return

            if self.path == "/api/apply":
                try:
                    self.send_json(app.apply_theme(document))
                except ThemeApplyError as exc:
                    self.send_json({
                        "ok": False,
                        "errors": [str(exc)],
                        "saved": exc.saved,
                    }, exc.status)
                return

            self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    return Handler


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--themes-dir", type=Path,
        help="theme directory; defaults to cwd when cwd contains themes, otherwise ./themes")
    parser.add_argument("--theme-py", type=Path, help="path to theme.py; auto-detected when omitted")
    parser.add_argument("--schema", type=Path, help="path to theme.schema.json; auto-detected when omitted")
    parser.add_argument(
        "--www-dir", type=Path,
        help="web asset directory; defaults to the www directory next to this file")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind address")
    parser.add_argument(
        "--url-host", help="host or IP printed in the editor URL; defaults to --host")
    parser.add_argument("--port", type=int, default=8765, help="local HTTP port, or 0 for any free port")
    parser.add_argument("--no-open", action="store_true", help="do not open the browser automatically")
    parser.add_argument("--quiet-http", action="store_true", help="do not print HTTP request logs")
    return parser.parse_args()


def main():
    args = parse_args()
    base = Path.cwd().resolve()
    themes_dir = discover_themes_dir(base, args.themes_dir)
    theme_py = args.theme_py.expanduser().resolve() if args.theme_py else discover_theme_py(base)
    schema = args.schema.expanduser().resolve() if args.schema else discover_schema(base, themes_dir)
    www_dir = (args.www_dir.expanduser().resolve()
               if args.www_dir else DEFAULT_WWW_DIR.resolve())
    if not www_dir.is_dir():
        raise SystemExit("--www-dir is not a directory: %s" % www_dir)
    public_host = args.url_host or args.host

    app = App(themes_dir, theme_py, schema, www_dir=www_dir)
    themes = app.theme_list()
    server = ThreadingHTTPServer(
        (args.host, args.port), make_handler(app, not args.quiet_http))
    _bind_host, port = server.server_address
    url = "http://%s:%d/" % (public_host, port)

    print("Feather Theme Editor")
    print("  themes   : %s (%d found)" % (themes_dir, len(themes)))
    print("  theme.py : %s" % (theme_py or "not found; embedded contract"))
    print("  schema   : %s" % (schema or "not found; basic validation only"))
    print("  www      : %s" % www_dir)
    print("  url      : %s" % url)
    print("Press Ctrl+C to stop.", flush=True)

    if not args.no_open:
        threading.Timer(0.35, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
