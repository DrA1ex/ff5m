## Pre-render validated scenario scenes through the isolated Designer host.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

"""Pre-render validated scenario scenes through the isolated Designer host."""

import base64
import json
import pathlib
import pickle
import sys
from types import SimpleNamespace


def _state_metadata(scene):
    return dict(
        (str(item.get("key")), item)
        for item in scene.get("state_schema", ())
        if isinstance(item, dict) and item.get("key"))


def _assert_requested_state(case, scene):
    metadata = _state_metadata(scene)
    for key, expected in (case.get("state") or {}).items():
        item = metadata.get(str(key))
        if item is None or not item.get("value_available"):
            raise ValueError(
                "Designer scene omitted requested state key: %s" % key)
        if item.get("value") != expected:
            raise ValueError(
                "Designer scene did not apply requested state key: %s"
                % key)


def _render_dialog_fixture(scene, fixture, project_root, theme):
    plugins = project_root / ".py" / "klipper" / "plugins"
    sys.path.insert(0, str(plugins.resolve()))
    from feather_preview.ui import PreviewRenderer

    renderer = PreviewRenderer(width=800, height=480)
    renderer.set_theme(theme)
    commands = renderer.begin_page("Dialog layout")
    commands += renderer.dialog(
        fixture["title"], fixture["lines"], fixture["buttons"],
        x=160, y=130, width=480, height=220, tone="info",
        page=fixture.get("page", 0),
        page_actions=("dialog.test.prev", "dialog.test.next"))
    scene["operations"] = commands
    scene["title"] = "Dialog layout / " + fixture["title"]
    scene["palette"] = renderer.palette
    scene["diagnostics"] = []
    return scene


def _render_message_fixture(scene, fixture, project_root, theme):
    """Render the product's message dialog with the Designer renderer."""
    plugins = project_root / ".py" / "klipper" / "plugins"
    sys.path.insert(0, str(plugins.resolve()))
    from feather_preview.ui import PreviewRenderer
    from feather_screen import FeatherScreen

    renderer = PreviewRenderer(width=800, height=480)
    renderer.set_theme(theme)
    captured = []
    renderer.send = lambda commands: captured.extend(commands)
    screen = FeatherScreen.__new__(FeatherScreen)
    screen.renderer = renderer
    screen.message = fixture["message"]
    screen.message_title = fixture.get("title")
    screen.message_actions = tuple(
        tuple(button) for button in fixture.get(
            "buttons", (("message.ok", "OK", "enabled"),)))
    screen.message_page = fixture.get("page", 0)
    screen._render_message()
    scene["operations"] = captured
    scene["title"] = "Message / " + (screen.message_title or screen.message)
    scene["palette"] = renderer.palette
    scene["diagnostics"] = []
    return scene


def _render_error_fixture(scene, fixture, project_root, theme):
    """Render the product's error dialog with the Designer renderer."""
    plugins = project_root / ".py" / "klipper" / "plugins"
    sys.path.insert(0, str(plugins.resolve()))
    from feather_preview.ui import PreviewRenderer
    from feather_screen import FeatherScreen

    renderer = PreviewRenderer(width=800, height=480)
    renderer.set_theme(theme)
    captured = []
    renderer.send = lambda commands: captured.extend(commands)
    renderer.prioritize_next_batch = lambda *args: None
    screen = FeatherScreen.__new__(FeatherScreen)
    screen.renderer = renderer
    screen.error_message = fixture["message"]
    screen.error_recovery = fixture["recovery"]
    screen.error_page = fixture.get("page", 0)
    screen._render_error()
    scene["operations"] = captured
    scene["title"] = "Klipper error / " + fixture["message"].splitlines()[0]
    scene["palette"] = renderer.palette
    scene["diagnostics"] = []
    return scene


def _render_ota_fixture(scene, fixture, project_root, theme):
    """Render the product's actual update dialog through Designer primitives."""
    plugins = project_root / ".py" / "klipper" / "plugins"
    sys.path.insert(0, str(plugins.resolve()))
    from feather_preview.ui import PreviewRenderer
    from feather.update_notification import ForgeXUpdateNotification

    renderer = PreviewRenderer(width=800, height=480)
    renderer.set_theme(theme)
    captured = []
    renderer.send = lambda commands: captured.extend(commands)
    notification = ForgeXUpdateNotification(
        SimpleNamespace(renderer=renderer, reactor=None), None)
    notification.installed_version = fixture["installed_version"]
    notification.available_version = fixture["available_version"]
    notification.changes = tuple(fixture["changes"])
    notification.recovery_files = tuple(fixture.get("recovery_files", ()))
    notification.change_page = fixture.get("page", 0)
    notification.render()
    scene["operations"] = captured
    scene["title"] = "OTA update / " + fixture["available_version"]
    scene["palette"] = renderer.palette
    scene["diagnostics"] = []
    return scene


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 3:
        raise SystemExit(
            "usage: designer_scenes.py DESIGNER_ROOT PROJECT_ROOT PLAN")
    designer_root, project_root, plan_path = map(pathlib.Path, argv)
    sys.path.insert(0, str(designer_root.resolve()))
    from feather_preview.host_client import ProjectHostClient

    plan_path = plan_path.resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    client = ProjectHostClient(
        project_root.resolve(), theme="DEFAULT", width=800, height=480,
        data_root=plan_path.parent / ".designer-host-data")
    try:
        encoded = client.call("host.checkpoint")["checkpoint"]
        baseline = pickle.loads(base64.b64decode(encoded.encode("ascii")))
        for case in plan["cases"]:
            checkpoint = pickle.loads(pickle.dumps(
                baseline, protocol=pickle.HIGHEST_PROTOCOL))
            screen = case["semantic_page_id"]
            state = checkpoint["states"].get(screen)
            if state is None:
                raise ValueError("unknown scenario page: %s" % screen)
            state.update(case.get("state") or {})
            restored = base64.b64encode(pickle.dumps(
                checkpoint, protocol=pickle.HIGHEST_PROTOCOL)).decode("ascii")
            client.call("host.restore", {"checkpoint": restored})
            # Restoring a checkpoint correctly applies read-only page state,
            # but the Designer simulator owns values with simulation roles
            # (positions, homing, movement step, inertia) and restores its own
            # model afterward. Re-apply every mutable requested value through
            # the public state API so the simulator and page state agree.
            viewport = {
                "width": case["width"], "height": case["height"],
            }
            scene = client.call("page.render", {
                "screen": screen,
                "theme": case["theme"],
                "viewport": viewport,
            })
            metadata = _state_metadata(scene)
            unknown = sorted(
                set(case.get("state") or {}) - set(metadata))
            if unknown:
                raise ValueError(
                    "unknown Designer scenario state key: %s" % unknown[0])
            for key, value in (case.get("state") or {}).items():
                if not metadata[key].get("mutable"):
                    continue
                client.call("state.update", {
                    "screen": screen,
                    "key": key,
                    "value": value,
                    "theme": case["theme"],
                    "viewport": viewport,
                })
            for action in case.get("actions") or ():
                client.call("action.dispatch", {
                    "screen": screen,
                    "action": action["wire_id"],
                    "event": action.get("event") or {},
                })
            case["scene"] = client.call("page.render", {
                "screen": screen,
                "theme": case["theme"],
                "viewport": viewport,
            })
            _assert_requested_state(case, case["scene"])
            if case.get("dialog_fixture") is not None:
                case["scene"] = _render_dialog_fixture(
                    case["scene"], case["dialog_fixture"],
                    project_root, case["theme"])
            if case.get("message_fixture") is not None:
                case["scene"] = _render_message_fixture(
                    case["scene"], case["message_fixture"],
                    project_root, case["theme"])
            if case.get("error_fixture") is not None:
                case["scene"] = _render_error_fixture(
                    case["scene"], case["error_fixture"],
                    project_root, case["theme"])
            if case.get("ota_fixture") is not None:
                case["scene"] = _render_ota_fixture(
                    case["scene"], case["ota_fixture"],
                    project_root, case["theme"])
    finally:
        client.close()
    plan_path.write_text(
        json.dumps(plan, separators=(",", ":"), ensure_ascii=True),
        encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
