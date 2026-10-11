## Observable print-menu and object-exclusion contracts.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import copy
import unittest
from types import SimpleNamespace
from unittest import mock

from tests.feather_exclude_test_helper import hit_at, make_screen, tap
from feather.screen.pages.exclude_objects import bed_object_rect, object_action, polygon_bounds
from ff5m_ui.exclude_model import PAGE
from ff5m_ui.screen import ScreenDialog, ScreenPage
from tests.feather_render_test_helper import full_render_queue
from ui import ThemeColor
from ui.font_metrics import get_font_metrics


class ExcludeObjectsTest(unittest.TestCase):
    def setUp(self):
        self.screen = make_screen()

    def open_objects(self):
        tap(self.screen, "print.more")
        tap(self.screen, "more.exclude")
        self.assertEqual(self.screen.page, ScreenPage.EXCLUDE_MODEL)
        self.assertIsNone(self.screen._current_dialog())

    def select_and_request(self, name="Gear_Cover"):
        self.open_objects()
        tap(self.screen, object_action(name))
        tap(self.screen, "exclude.request")

    def test_bottom_controls_and_popup_close_outside_without_activating_page(self):
        controls = self.screen.capture.latest.buttons
        bottom = sorted((b for b in controls.values() if b.action.startswith("print.")), key=lambda b: b.bounds.x)
        self.assertEqual([b.label for b in bottom], ["PAUSE", "Z ADJUST", "MORE", "CANCEL"])
        self.assertFalse(self.screen.capture.latest.has_action("print.preview"))
        tap(self.screen, "print.more")
        frame = self.screen.capture.latest
        self.assertEqual(self.screen._current_dialog(), ScreenDialog.PRINT_MORE)
        self.assertEqual(frame.button("more.filament").label, "Change Filament")
        self.assertEqual(frame.button("more.exclude").label, "Exclude Model")
        self.assertLess(frame.button("more.exclude").bounds.bottom, bottom[2].bounds.y)
        self.assertTrue(frame.has_action("global.abort"))
        abort = frame.button("global.abort").bounds
        self.assertEqual(hit_at(self.screen, abort.x + 10, abort.y + 10), "global.abort")
        action = hit_at(self.screen, bottom[-1].bounds.x + 10, bottom[-1].bounds.y + 10)
        self.assertEqual(action, "more.close")
        tap(self.screen, action)
        self.assertIsNone(self.screen._current_dialog())
        self.assertTrue(self.screen.capture.latest.has_action("print.cancel"))
        self.assertEqual(self.screen.gcode.commands, [])

    def test_abort_remains_immediate_with_more_open(self):
        tap(self.screen, "print.more")
        self.screen._run_immediate_command = mock.Mock()
        abort = self.screen.capture.latest.button("global.abort").bounds
        tap(self.screen, hit_at(self.screen, abort.x + 10, abort.y + 10))
        self.screen._run_immediate_command.assert_called_once_with("M112")

    def test_filament_closes_popup_and_uses_existing_print_interaction(self):
        tap(self.screen, "print.more")
        with mock.patch.object(self.screen, "_handle_print_action") as change:
            tap(self.screen, "more.filament")
        change.assert_called_once_with("print.filament")
        self.assertIsNone(self.screen._current_dialog())

    def test_rejected_popup_frame_preserves_print_controls_until_retry(self):
        with full_render_queue(self.screen.renderer):
            tap(self.screen, "print.more")
        self.assertIsNone(self.screen._current_dialog())
        self.assertTrue(any(r.action == "print.cancel" for r in self.screen.renderer.hitboxes))
        self.screen._render_screen()
        self.assertEqual(self.screen._current_dialog(), ScreenDialog.PRINT_MORE)

    def test_object_labels_and_statuses_fit_their_touch_rows(self):
        self.open_objects()
        frame = self.screen.capture.latest
        for item in self.screen.objects.status["objects"]:
            row = max((region for region in self.screen.renderer.hitboxes
                       if region.action == object_action(item["name"])), key=lambda region: region.x)
            texts = [t for t in frame.texts if row.x <= t.x < row.x + row.width
                     and row.y <= t.y < row.y + row.height]
            self.assertEqual(len(texts), 2)
            for text in texts:
                metrics = get_font_metrics().metric(text.font)
                self.assertGreaterEqual(text.y - metrics.glyph_height // 2, row.y + 2)
                self.assertLessEqual(text.y + metrics.glyph_height // 2, row.y + row.height - 2)

    def test_object_rows_keep_their_height_with_fewer_items_and_on_last_page(self):
        heights = []
        original = self.screen.objects.status["objects"][0]
        for count in (1, 2, 3, 4, 7):
            with self.subTest(count=count):
                self.screen = make_screen()
                self.screen.objects.status["objects"] = [dict(original, name="Part_%d" % i)
                                                         for i in range(count)]
                self.open_objects()
                for page in range((count + 2) // 3):
                    rows = sorted((r for r in self.screen.renderer.hitboxes
                                   if r.action.startswith("exclude.select.")
                                   and r.x > PAGE.layout["exclude.bed"].right), key=lambda r: r.y)
                    self.assertEqual(len(rows), min(3, count - page * 3))
                    for row in rows:
                        heights.append(row.height)
                        self.assertGreaterEqual(row.height, 52)
                        self.assertLessEqual(row.height, 80)
                        self.assertLess(row.y + row.height, PAGE.layout["exclude.request"].y)
                    for first, second in zip(rows, rows[1:]):
                        self.assertLess(first.y + first.height, second.y)
                    if page + 1 < (count + 2) // 3:
                        tap(self.screen, "exclude.next")
        self.assertEqual(len(set(heights)), 1)

    def test_footprint_scale_uses_printable_bed_instead_of_parking_travel(self):
        self.screen.move_safe.status.update(x_min=-110, y_min=-110, x_max=110, y_max=110)
        self.screen.toolhead.status.update(axis_minimum=(-125, -125, 0, 0),
                                          axis_maximum=(125, 125, 230, 0))
        self.screen.objects.status["objects"] = [
            dict(name="Half_Bed", polygon=[[-110, -110], [0, -110], [0, 0], [-110, 0]]),
            dict(name="Quarter_Bed", polygon=[[55, 55], [110, 55], [110, 110], [55, 110]]),
        ]
        self.open_objects()
        bed = PAGE.layout["exclude.bed"]
        for name, fraction, left, top in (("Half_Bed", .5, 0, .5), ("Quarter_Bed", .25, .75, 0)):
            region = min((r for r in self.screen.renderer.hitboxes if r.action == object_action(name)),
                         key=lambda r: r.x)
            self.assertAlmostEqual(region.width, bed.width * fraction, delta=1)
            self.assertAlmostEqual(region.height, bed.height * fraction, delta=1)
            self.assertAlmostEqual(region.x, bed.x + bed.width * left, delta=1)
            self.assertAlmostEqual(region.y, bed.y + bed.height * top, delta=1)

    def test_unavailable_printable_bounds_keep_models_list_only(self):
        for limits in (None, {}, dict(x_min=-110, y_min=-110, x_max=-110, y_max=110),
                       dict(x_min=-110, y_min=-110, x_max=float("nan"), y_max=110)):
            with self.subTest(limits=limits):
                self.screen = make_screen()
                if limits is None:
                    self.screen.printer.lookup_object = lambda name, default=None: (
                        self.screen.objects if name == "exclude_object" else default)
                else:
                    self.screen.move_safe.status = limits
                self.open_objects()
                self.assertTrue(self.screen.capture.latest.has_text("Geometry unavailable"))
                for model in self.screen.objects.status["objects"]:
                    regions = [r for r in self.screen.renderer.hitboxes
                               if r.action == object_action(model["name"])]
                    self.assertEqual(len(regions), 1)

    def test_rectangular_bed_keeps_equal_xy_scale_and_centers_shorter_axis(self):
        rectangle = bed_object_rect((20, 10, 60, 50), (0, 0, 200, 100))
        for actual, expected in zip(rectangle, (.1, .5, .2, .2)):
            self.assertAlmostEqual(actual, expected)
        self.assertIsNone(bed_object_rect((-1, 0, 60, 50), (0, 0, 200, 100)))

    def test_excluded_card_is_gray_and_both_pick_regions_remain_active(self):
        self.open_objects()
        action = object_action("Cable_Clip")
        regions = [region for region in self.screen.renderer.hitboxes if region.action == action]
        self.assertEqual(len(regions), 2)
        card = max(regions, key=lambda region: region.x)
        bounds = (card.x, card.y, card.width, card.height)
        commands = self.screen.capture.batches[-1]
        self.assertIn(self.screen.renderer.stroke(*bounds, color=ThemeColor.DIM, line_width=2), commands)
        self.assertIn(self.screen.renderer.fill(*bounds, color=ThemeColor.PANEL), commands)
        for index, region in enumerate(regions):
            tap(self.screen, hit_at(self.screen, region.x + region.width // 2,
                                   region.y + region.height // 2))
            self.assertEqual(self.screen.exclude_selected, {"Cable_Clip"} if index == 0 else set())
            self.assertFalse(self.screen.capture.latest.has_action("exclude.request"))

    def test_bed_and_list_share_selection_and_page_follows_bed_pick(self):
        self.open_objects()
        regions = [r for r in self.screen.renderer.hitboxes if r.action == object_action("Gear_Cover")]
        self.assertEqual(len(regions), 2)
        bed, row = sorted(regions, key=lambda r: r.x)
        tap(self.screen, hit_at(self.screen, bed.x + bed.width // 2, bed.y + bed.height // 2))
        self.assertEqual(self.screen.exclude_selected, {"Gear_Cover"})
        self.assertTrue(self.screen.capture.latest.has_action(object_action("Gear_Cover")))
        self.assertTrue(self.screen.capture.latest.has_text("SELECTED"))
        tap(self.screen, object_action("Bracket_Main"))
        tap(self.screen, hit_at(self.screen, row.x + 20, row.y + 20))
        self.assertEqual(self.screen.exclude_selected, {"Bracket_Main"})
        self.assertTrue(self.screen.capture.latest.has_action("exclude.request"))
        self.assertTrue(self.screen.capture.latest.has_text("EXCLUDED"))

    def test_confirmation_cancel_sends_nothing_then_accept_observes_excluded_state(self):
        self.select_and_request()
        self.assertEqual(self.screen._current_dialog(), ScreenDialog.MESSAGE)
        self.assertEqual(self.screen.gcode.commands, [])
        tap(self.screen, "message.ok")
        self.assertEqual(self.screen.gcode.commands, [])
        tap(self.screen, "exclude.request")
        tap(self.screen, "exclude.confirm")
        self.assertEqual(self.screen.gcode.commands, ["EXCLUDE_OBJECT NAME=Gear_Cover"])
        self.assertIsNone(self.screen._current_dialog())
        self.assertFalse(self.screen.capture.latest.has_action("exclude.request"))
        tap(self.screen, "exclude.request")
        self.assertEqual(len(self.screen.gcode.commands), 1)

    def test_excluded_object_can_be_selected_and_is_still_explicitly_excluded(self):
        self.open_objects()
        tap(self.screen, object_action("Cable_Clip"))
        self.assertEqual(self.screen.exclude_selected, {"Cable_Clip"})
        self.assertTrue(self.screen.capture.latest.has_text("SELECTED / EXCLUDED"))
        self.assertTrue(self.screen.capture.latest.has_text("3"))
        self.assertFalse(self.screen.capture.latest.has_action("exclude.request"))

    def test_confirmation_is_bound_to_object_and_active_file(self):
        for change in (lambda s: setattr(s.virtual_sdcard, "path", "/different.gcode"),
                       lambda s: s.objects.status.update(objects=[]),
                       lambda s: s.print_stats.status.update(state="complete")):
            with self.subTest(change=change):
                self.screen = make_screen()
                self.select_and_request()
                change(self.screen)
                with self.assertLogs(level="ERROR"):
                    tap(self.screen, "exclude.confirm")
                self.assertEqual(self.screen.gcode.commands, [])
                self.assertIn("no longer part", self.screen._current_dialog_instance().content["message"])

    def test_command_failure_is_reported_and_does_not_mark_object_excluded(self):
        self.select_and_request()
        self.screen.gcode.run_script = mock.Mock(side_effect=RuntimeError("Exclusion failed"))
        with self.assertLogs(level="ERROR"):
            tap(self.screen, "exclude.confirm")
        self.assertNotIn("Gear_Cover", self.screen.objects.status["excluded_objects"])
        self.assertEqual(self.screen._current_dialog_instance().content["message"], "Exclusion failed")
        tap(self.screen, "message.ok")
        self.assertTrue(self.screen.capture.latest.has_action("exclude.request"))

    def test_missing_geometry_keeps_honest_selectable_list(self):
        self.screen.objects.status["objects"] = [{"name": "Unknown_Size", "center": [50, 60]}]
        self.open_objects()
        self.assertTrue(self.screen.capture.latest.has_text("Geometry unavailable"))
        self.assertTrue(self.screen.capture.latest.has_text("TAP TO SELECT / NO GEOMETRY"))
        regions = [r for r in self.screen.renderer.hitboxes if r.action == object_action("Unknown_Size")]
        self.assertEqual(len(regions), 1)
        tap(self.screen, object_action("Unknown_Size"))
        self.assertTrue(self.screen.capture.latest.has_action("exclude.request"))

    def test_missing_plugin_or_empty_objects_is_readable_and_cannot_exclude(self):
        self.screen.printer.lookup_object = lambda _name, default=None: default
        self.open_objects()
        self.assertTrue(self.screen.capture.latest.has_text("No objects defined in this file"))
        self.assertFalse(self.screen.capture.latest.has_action("exclude.request"))

    def test_protocol_preserves_unicode_spaces_quotes_and_parameter_like_names(self):
        for name in ("Крышка шестерни", "Gear Cover", "Gear_Cover RESET=1",
                     'Корпус "левый"', "Крышка O'Brien", r"Bracket\Cover", " Модель  1 ",
                     "Деталь (левая).stl", "Модель CURRENT=1 RESET=1"):
            with self.subTest(name=name):
                self.screen = make_screen()
                self.screen.objects.status["objects"][1]["name"] = name
                self.select_and_request(name)
                tap(self.screen, "exclude.confirm")
                self.assertIsNone(self.screen._current_dialog())
                self.assertIn(name, self.screen.objects.status["excluded_objects"])
                self.assertEqual(self.screen.gcode.parameters, [{"NAME": name}])

    def test_protocol_rejects_line_breaks_and_klipper_comment_delimiters(self):
        for name in ("Gear\nM112", "Gear\rCover", "Gear\x00Cover", "Gear; RESET=1",
                     "Gear#Cover", "Gear*Cover", "Gear\tCover", "Gear\x7fCover"):
            with self.subTest(name=name):
                self.screen = make_screen()
                self.screen.objects.status["objects"][1]["name"] = name
                self.select_and_request(name)
                with self.assertLogs(level="ERROR"):
                    tap(self.screen, "exclude.confirm")
                self.assertEqual(self.screen.gcode.commands, [])
                self.assertIn("unsupported by Klipper", self.screen._current_dialog_instance().content["message"])

    def test_multiple_selection_from_map_and_list_confirms_the_whole_set(self):
        self.open_objects()
        bed = min((r for r in self.screen.renderer.hitboxes
                   if r.action == object_action("Gear_Cover")), key=lambda r: r.x)
        tap(self.screen, hit_at(self.screen, bed.x + bed.width // 2, bed.y + bed.height // 2))
        row = max((r for r in self.screen.renderer.hitboxes
                   if r.action == object_action("Bracket_Main")), key=lambda r: r.x)
        tap(self.screen, hit_at(self.screen, row.x + 20, row.y + 20))
        self.assertEqual(self.screen.exclude_selected, {"Gear_Cover", "Bracket_Main"})
        self.assertEqual(self.screen.capture.latest.button("exclude.request").label, "EXCLUDE SELECTED (2)")
        self.assertEqual(self.screen.gcode.commands, [])
        tap(self.screen, "exclude.request")
        confirmation = self.screen._current_dialog_instance().content
        self.assertEqual(confirmation["title"], "Exclude 2 models?")
        for name in ("Bracket Main", "Gear Cover"):
            self.assertIn(name, confirmation["message"])
        tap(self.screen, "message.ok")
        self.assertEqual(self.screen.gcode.commands, [])
        self.assertEqual(self.screen.exclude_selected, {"Gear_Cover", "Bracket_Main"})
        tap(self.screen, "exclude.request")
        tap(self.screen, "exclude.confirm")
        self.assertEqual(self.screen.gcode.parameters, [{"NAME": "Bracket_Main"}, {"NAME": "Gear_Cover"}])
        self.assertFalse(self.screen.capture.latest.has_action("exclude.request"))

    def test_second_tap_deselects_and_selection_survives_list_pagination(self):
        self.screen.objects.status["objects"].append(self.screen.objects.status["tiny_object"])
        self.open_objects()
        tap(self.screen, object_action("Gear_Cover"))
        tap(self.screen, "exclude.next")
        tap(self.screen, object_action("Tiny_Pin"))
        tap(self.screen, "exclude.prev")
        self.assertEqual(self.screen.exclude_selected, {"Gear_Cover", "Tiny_Pin"})
        tap(self.screen, object_action("Gear_Cover"))
        self.assertEqual(self.screen.exclude_selected, {"Tiny_Pin"})
        tap(self.screen, "exclude.next")
        tap(self.screen, object_action("Tiny_Pin"))
        self.assertEqual(self.screen.exclude_selected, set())
        self.assertFalse(self.screen.capture.latest.has_action("exclude.request"))

    def test_many_unicode_models_on_paused_print_use_paginated_confirmation(self):
        names = ["Крышка %02d" % index for index in range(12)]
        self.screen.objects.status["objects"] = [{"name": name} for name in names]
        self.screen.print_stats.status["state"] = "paused"
        self.open_objects()
        for index, name in enumerate(names):
            if index and index % 3 == 0:
                tap(self.screen, "exclude.next")
            tap(self.screen, object_action(name))
        self.assertEqual(self.screen.capture.latest.button("exclude.request").label, "EXCLUDE SELECTED (12)")
        tap(self.screen, "exclude.request")
        self.assertTrue(self.screen.capture.latest.has_action("message.next"))
        for _ in range(20):
            if not self.screen.capture.latest.has_action("message.next"):
                break
            tap(self.screen, "message.next")
        self.assertFalse(self.screen.capture.latest.has_action("message.next"))
        self.assertEqual(self.screen.gcode.commands, [])
        tap(self.screen, "exclude.confirm")
        self.assertEqual(self.screen.gcode.parameters, [{"NAME": name} for name in names])

    def test_batch_ignores_excluded_models_and_rechecks_external_exclusions(self):
        self.open_objects()
        for name in ("Bracket_Main", "Gear_Cover", "Cable_Clip"):
            tap(self.screen, object_action(name))
        self.assertEqual(self.screen.capture.latest.button("exclude.request").label, "EXCLUDE SELECTED (2)")
        tap(self.screen, "exclude.request")
        self.screen.objects.status["excluded_objects"].append("Bracket_Main")
        tap(self.screen, "exclude.confirm")
        self.assertEqual(self.screen.gcode.parameters, [{"NAME": "Gear_Cover"}])

    def test_confirmation_uses_its_snapshot_and_validates_all_names_before_sending(self):
        self.open_objects()
        for name in ("Bracket_Main", "Gear_Cover"):
            tap(self.screen, object_action(name))
        tap(self.screen, "exclude.request")
        self.screen.exclude_selected = {"Cable_Clip"}
        tap(self.screen, "exclude.confirm")
        self.assertEqual(self.screen.gcode.parameters, [{"NAME": "Bracket_Main"}, {"NAME": "Gear_Cover"}])
        for bad_name in ("Gear;Cover", "Missing"):
            with self.subTest(bad_name=bad_name):
                self.screen = make_screen()
                self.screen.objects.status["objects"][1]["name"] = bad_name
                self.open_objects()
                for name in ("Bracket_Main", bad_name):
                    tap(self.screen, object_action(name))
                tap(self.screen, "exclude.request")
                if bad_name == "Missing":
                    self.screen.objects.status["objects"].pop(1)
                with self.assertLogs(level="ERROR"):
                    tap(self.screen, "exclude.confirm")
                self.assertEqual(self.screen.gcode.commands, [])

    def test_partial_batch_failure_retries_only_models_still_printing(self):
        self.open_objects()
        for name in ("Bracket_Main", "Gear_Cover"):
            tap(self.screen, object_action(name))
        tap(self.screen, "exclude.request")
        run = self.screen.gcode.run_script

        def fail_second(command):
            if "Gear_Cover" in command:
                raise RuntimeError("Exclusion failed")
            run(command)

        self.screen.gcode.run_script = fail_second
        with self.assertLogs(level="ERROR"):
            tap(self.screen, "exclude.confirm")
        self.assertIn("Bracket_Main", self.screen.objects.status["excluded_objects"])
        self.assertNotIn("Gear_Cover", self.screen.objects.status["excluded_objects"])
        self.assertIn("Exclusion failed", self.screen._current_dialog_instance().content["message"])
        tap(self.screen, "message.ok")
        self.assertEqual(self.screen.capture.latest.button("exclude.request").label, "EXCLUDE SELECTED (1)")
        self.screen.gcode.run_script = run
        tap(self.screen, "exclude.request")
        tap(self.screen, "exclude.confirm")
        self.assertEqual(self.screen.gcode.parameters, [{"NAME": "Bracket_Main"}, {"NAME": "Gear_Cover"}])

    def test_print_change_between_batch_commands_stops_the_remaining_commands(self):
        for change in (lambda s: setattr(s.virtual_sdcard, "path", "/different.gcode"),
                       lambda s: s.objects.status["objects"].pop(1),
                       lambda s: s.print_stats.status.update(state="complete")):
            with self.subTest(change=change):
                self.screen = make_screen()
                self.open_objects()
                for name in ("Bracket_Main", "Gear_Cover"):
                    tap(self.screen, object_action(name))
                tap(self.screen, "exclude.request")
                run = self.screen.gcode.run_script

                def change_print(command):
                    run(command)
                    change(self.screen)

                self.screen.gcode.run_script = change_print
                with self.assertLogs(level="ERROR"):
                    tap(self.screen, "exclude.confirm")
                self.assertEqual(self.screen.gcode.parameters, [{"NAME": "Bracket_Main"}])

    def test_render_drops_missing_selections_and_reopening_starts_empty(self):
        self.open_objects()
        for name in ("Bracket_Main", "Gear_Cover"):
            tap(self.screen, object_action(name))
        self.screen.objects.status["objects"].pop(1)
        self.screen._render_screen()
        self.assertEqual(self.screen.exclude_selected, {"Bracket_Main"})
        tap(self.screen, "exclude.back")
        self.open_objects()
        self.assertEqual(self.screen.exclude_selected, set())

    def test_pagination_and_reordering_do_not_redirect_selection(self):
        extra = [dict(self.screen.objects.status["objects"][0], name="Part_%d" % i) for i in range(7)]
        self.screen.objects.status["objects"].extend(extra)
        self.open_objects()
        tap(self.screen, "exclude.next")
        self.assertTrue(self.screen.capture.latest.has_action(object_action("Part_1")))
        self.screen.objects.status["objects"].reverse()
        tap(self.screen, object_action("Part_1"))
        self.assertEqual(self.screen.exclude_selected, {"Part_1"})
        self.assertTrue(self.screen.capture.latest.has_action(object_action("Part_1")))

    def test_tiny_map_label_shrinks_without_changing_selection_target(self):
        fixture = self.screen.objects.status
        fixture["objects"].append(fixture["tiny_object"])
        self.open_objects()
        label = next(text for text in self.screen.capture.latest.texts if text.value == "4")
        self.assertEqual(label.font, "JetBrainsMono Bold 8pt")
        region = next(region for region in self.screen.renderer.hitboxes
                      if region.action == object_action("Tiny_Pin"))
        tap(self.screen, hit_at(self.screen, region.x + region.width // 2,
                               region.y + region.height // 2))
        self.assertEqual(self.screen.exclude_selected, {"Tiny_Pin"})
        self.assertTrue(self.screen.capture.latest.has_text("SELECTED"))
        self.assertEqual(self.screen.gcode.commands, [])

    def test_active_and_selected_objects_use_primary_and_secondary_colors(self):
        self.open_objects()
        tap(self.screen, object_action("Gear_Cover"))
        commands = self.screen.capture.batches[-1]
        for name, color in (("Bracket_Main", ThemeColor.PRIMARY), ("Gear_Cover", ThemeColor.SECONDARY)):
            for region in (r for r in self.screen.renderer.hitboxes if r.action == object_action(name)):
                bounds = region.x, region.y, region.width, region.height
                width = 4 if region.x < 370 and name == "Gear_Cover" else 2
                self.assertIn(self.screen.renderer.stroke(*bounds, color=color, line_width=width), commands)

    def test_native_visual_snapshots_restore_inputs_and_issue_no_gcode(self):
        from feather_ui_test.scenarios import ScenarioCatalog
        catalog = ScenarioCatalog(SimpleNamespace(host=self.screen))
        original = copy.deepcopy(self.screen.objects.status)
        first_frame = len(self.screen.capture.frames)
        catalog._render_print_more_snapshot()
        self.assertEqual(self.screen._current_dialog(), ScreenDialog.PRINT_MORE)
        self.assertTrue(any(frame.has_text("PRINTING") for frame in self.screen.capture.frames[first_frame:]))
        for kind in ("selected", "excluded", "small", "empty", "no-geometry", "paginated"):
            with self.subTest(kind=kind):
                catalog._render_exclude_snapshot(kind)
                self.assertEqual(self.screen.objects.status, original)
                self.assertEqual(self.screen.gcode.commands, [])
                frame = self.screen.capture.latest
                self.assertTrue(frame.has_text("EXCLUDE MODEL"))
                self.assertFalse(any(text.value.endswith(" X") for text in frame.texts))
                if kind == "small":
                    self.assertEqual(next(t.font for t in frame.texts if t.value == "4"),
                                     "JetBrainsMono Bold 8pt")
                if kind == "empty":
                    self.assertTrue(frame.has_text("No objects defined in this file"))
                if kind == "no-geometry":
                    self.assertTrue(frame.has_text("TAP TO SELECT / NO GEOMETRY"))

    def test_geometry_validation_never_invents_extents(self):
        self.assertEqual(polygon_bounds([[1, 2], [3, 2], [2, 5]]), (1, 2, 3, 5))
        for value in (None, [], [[1, 2]], [[1, 2], [1, 2], [1, 2]],
                      [[0, 0], [float("nan"), 1], [1, 2]], [[1, 2, 3]] * 3):
            with self.subTest(value=value):
                self.assertIsNone(polygon_bounds(value))

    def test_back_returns_to_current_print_state(self):
        self.open_objects()
        self.screen.print_stats.status["state"] = "paused"
        tap(self.screen, "exclude.back")
        self.assertEqual(self.screen.page, ScreenPage.PAUSED)


if __name__ == "__main__":
    unittest.main()
