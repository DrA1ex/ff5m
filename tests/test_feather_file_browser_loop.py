## The file browser drawn by the frame loop.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import struct
import unittest

from tests.test_feather_workflows import FEATHER, FILES, base_controller
from tests.feather_render_test_helper import shown_actions
from tests.feather_typer_loop import TyperLoop, wait_for
from feather_ui_test import runner as UI_TEST

from feather.previews import PREVIEW_MASK_SIZE
from ff5m_ui import file_browser as file_browser_ui
from ff5m_ui.file_browser import TILE_HEIGHT, TILE_WIDTH, TILE_X, TILE_Y
from ui.theme import ThemeRole


WIDTH, HEIGHT = PREVIEW_MASK_SIZE


def mask(fill):
    """A one-bit preview raster."""
    packed = bytes((fill,)) * ((WIDTH * HEIGHT + 7) // 8)
    return (struct.pack("<4sBBBBHHII", b"FXI1", 1, 0, 2, 1, WIDTH, HEIGHT, len(packed), len(packed))
            + struct.pack("<II", 0, 0xff35d9e6) + packed)


class PendingWorker:
    def __init__(self):
        self.callbacks = []

    def submit(self, task, callback):
        self.callbacks.append(callback)
        return True


def entries(count):
    return [FILES.FileEntry("part-%d.gcode" % index, "/data/part-%d.gcode" % index, size=100 + index, mtime=10 + index)
            for index in range(count)]


class FileBrowserOnTheLoopTest(unittest.TestCase):
    def browser(self, view, *launches):
        """A real screen over the loop, with the files of the internal source found."""
        controller = base_controller()
        for method in ("_paint_page", "_render_screen", "_render_dialog", "_paint_page_values"):
            controller.__dict__.pop(method, None)
        controller._show_touch_unavailable = lambda: None
        controller.renderer = FEATHER.FeatherRenderer()
        typer = TyperLoop(controller.renderer, *launches)
        self.addCleanup(typer.close)
        typer.start()
        controller.file_view = view
        controller.file_worker = PendingWorker()
        controller.file_entry_cache["internal"] = entries(4)
        return controller, typer

    def preview_done(self, controller, fill):
        controller.file_worker.callbacks[-1]({
            "image": {}, "color": controller.renderer.color(FEATHER.ThemeColor.PRIMARY), "blob": mask(fill)}, None)

    def test_the_loop_draws_the_list_and_its_pages_without_python(self):
        controller, typer = self.browser("list")
        with self.assertNoLogs(level="WARNING"):
            controller._show_page(FEATHER.ScreenPage.FILE_BROWSER)
            self.assertTrue(controller._screen_root.frames.attached)
            self.assertTrue(typer.drew("part-0.gcode"))
            self.assertTrue(wait_for(lambda: {"file.item3", "file.view.tiles", "file.refresh"}
                                     <= shown_actions(controller._screen_root)))
            self.assertNotIn("file.item4", shown_actions(controller._screen_root))

    def test_the_ui_test_opens_a_file_the_loop_drew(self):
        controller, typer = self.browser("list")
        controller._show_page(FEATHER.ScreenPage.FILE_BROWSER)
        self.assertTrue(wait_for(lambda: "file.item0" in shown_actions(controller._screen_root)))
        controller.last_action_time = -100.
        run = UI_TEST.UITestRun(controller)

        UI_TEST.ScenarioCatalog(run)._open_safe_file_confirm()

        self.assertEqual(controller.page, FEATHER.ScreenPage.FILE_CONFIRM)
        self.assertEqual(controller.selected_file.name, "part-0.gcode")

    def test_a_finished_scan_shows_the_tiles_and_starts_their_previews(self):
        controller, typer = self.browser("tiles")
        del controller.file_entry_cache["internal"]
        with self.assertNoLogs(level="WARNING"):
            controller._show_page(FEATHER.ScreenPage.FILE_BROWSER)
            scanned = controller.file_worker.callbacks[-1]
            scanned(entries(2), None)
            self.assertTrue(typer.drew("part-1"))
        self.assertEqual(len(controller.file_worker.callbacks), 2)

    def test_the_loop_repaints_only_the_tile_whose_preview_arrived(self):
        controller, typer = self.browser("tiles")
        with self.assertNoLogs(level="WARNING"):
            controller._show_page(FEATHER.ScreenPage.FILE_BROWSER)
            self.assertTrue(typer.drew("LOADING..."))
            frames = len(typer.history())
            self.preview_done(controller, 0x0f)
            drawn = typer.wait_for_batch(
                frames, lambda batch: any(getattr(command, "payload", None) == mask(0x0f) for command in batch))
            self.assertIsNotNone(drawn)

        panel = controller.renderer.panel(TILE_X[0], TILE_Y, TILE_WIDTH, TILE_HEIGHT,
                                          ThemeRole.BUTTON_BORDER, ThemeRole.BUTTON_BACKGROUND, 2)
        self.assertEqual(list(drawn[:2]), [str(command) for command in panel])
        self.assertTrue(any(getattr(command, "payload", None) == mask(0x0f) for command in drawn))
        # The other tiles and the page around them stay as they are.
        painted = [str(command) for command in drawn if "hitbox" not in str(command)]
        self.assertFalse(any("-p %d %d " % (TILE_X[1], TILE_Y) in command for command in painted))
        self.assertEqual(sum(command.startswith("--batch fill") for command in painted), 1)
        self.assertIn("file.item0", shown_actions(controller._screen_root))

    def test_the_loop_shows_a_pressed_tile_and_its_late_preview_keeps_the_press(self):
        controller, typer = self.browser("tiles")
        pressed_panel = controller.renderer.panel(TILE_X[0], TILE_Y, TILE_WIDTH, TILE_HEIGHT, FEATHER.ThemeColor.BRIGHT,
                                                  FEATHER.ThemeColor.PRESSED_BACKGROUND, 3)
        expected = [str(command) for command in pressed_panel]
        with self.assertNoLogs(level="WARNING"):
            controller._show_page(FEATHER.ScreenPage.FILE_BROWSER)
            self.assertTrue(typer.drew("LOADING..."))
            frames = len(typer.history())
            typer.tell("button %s down" % typer.wire("file.item0"))
            self.assertIsNotNone(typer.wait_for_batch(
                frames, lambda batch: list(batch[:2]) == expected))
            frames = len(typer.history())
            self.preview_done(controller, 0x3c)
            drawn = typer.wait_for_batch(
                frames, lambda batch: any(getattr(command, "payload", None) == mask(0x3c) for command in batch))
            self.assertIsNotNone(drawn)

        self.assertEqual(list(drawn[:2]), expected)
        self.assertTrue(any(getattr(command, "payload", None) == mask(0x3c) for command in drawn))


    def test_the_ui_test_captures_the_browser_once_its_scan_and_previews_are_in(self):
        controller, typer = self.browser("list")
        del controller.file_entry_cache["internal"]
        steps = UI_TEST.ScenarioCatalog(UI_TEST.UITestRun(controller)).build_steps("UI")
        labels = [step["label"] for step in steps]
        scanned = steps[labels.index("ui-files-scanned")]
        loaded = steps[labels.index("ui-files-previews-loaded")]
        show_tiles = next(step for step in steps if step["label"] == "ui-files-previews" and step["kind"] == "call")
        # Each wait comes before the capture it guards.
        self.assertLess(labels.index("ui-files-scanned"), labels.index("ui-files"))
        following = steps[labels.index("ui-files-previews-loaded") + 1]
        self.assertEqual((following["kind"], following["label"]), ("capture", "ui-files-previews"))

        controller._show_page(FEATHER.ScreenPage.FILE_BROWSER)
        self.assertFalse(scanned["predicate"]())
        controller.file_worker.callbacks[-1](entries(4), None)
        self.assertTrue(scanned["predicate"]())

        show_tiles["callback"]()
        self.assertTrue(typer.drew("LOADING..."))
        self.assertFalse(loaded["predicate"]())
        # The tiles of the first page load one at a time; a preview that fails shows NO PREVIEW, which is loaded too.
        controller.file_worker.callbacks[-1](None, RuntimeError("no thumbnail"))
        self.assertFalse(loaded["predicate"]())
        for _tile in range(len(file_browser_ui.TILES) - 1):
            self.preview_done(controller, 0x0f)
        self.assertTrue(loaded["predicate"]())

if __name__ == "__main__":
    unittest.main()
