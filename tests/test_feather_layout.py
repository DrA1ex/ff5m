## Tests for Feather declarative layouts and movement screens.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import pathlib
import sys
import unittest
from enum import Enum


PLUGINS = (pathlib.Path(__file__).parents[1] / ".py" / "klipper" /
           "plugins")
sys.path.insert(0, str(PLUGINS))

from ui import (  # noqa: E402
    EMPTY, FLEX, Button, ButtonStyle, Column, Command, CommandKey, Equal,
    FALLBACK_THEME, FeatherRenderer, Fill, Flex, Grid, Overlay, Override,
    PageKey, PageTree, Rect, Spacer, StateKey, Text, ThemeColor, ThemeRole,
    Tree, resolve_theme,
    WrapPanel, bind, state, subdivision_positions,
)
from ff5m_ui.move import runtime as move  # noqa: E402
from ff5m_ui.move import geometry as move_geometry  # noqa: E402
from ff5m_ui.heat import runtime as heat  # noqa: E402
from ff5m_ui.filament import runtime as filament  # noqa: E402
from ff5m_ui.z_offset import runtime as z_offset  # noqa: E402


class TestPage(PageKey):
    LAYOUT = "test.layout"


class TestState(StateKey):
    LEFT = state(str, default="A")
    RIGHT = state(str, default="B")


class TestCommand(CommandKey):
    ONE = "test.one"
    TWO = "test.two"
    THREE = "test.three"
    ACTION = "test.action"


def test_action(key=TestCommand.ACTION):
    return Command(key)


class RectLayoutTest(unittest.TestCase):
    def test_fixed_and_flexible_tracks_consume_container_exactly(self):
        rect = Rect(10, 20, 101, 40)

        first, second, third = rect.row(20, None, None, gap=3)

        self.assertEqual(first.as_tuple(), (10, 20, 20, 40))
        self.assertEqual(second.as_tuple(), (33, 20, 38, 40))
        self.assertEqual(third.as_tuple(), (74, 20, 37, 40))
        self.assertEqual(third.right, rect.right)

    def test_binary_subdivisions_include_real_endpoints_and_midpoint(self):
        positions = subdivision_positions(103, 314, 3)

        self.assertEqual([item[1] for item in positions],
                         [103, 142, 182, 221, 260, 299, 338, 378, 417])
        self.assertEqual(positions[0][2], -1)
        self.assertEqual(positions[4][2], 0)
        self.assertEqual(positions[-1][2], -1)


class DeclarativeContainerTest(unittest.TestCase):
    def test_column_auto_gap_distributes_all_free_space_between_fixed_children(self):
        tree = Tree(Column(
            Fill(ThemeColor.PRIMARY).height(10).ref("first"),
            Fill(ThemeColor.SECONDARY).height(10).ref("second"),
            Fill(ThemeColor.WARNING).height(10).ref("third"),
            gap=None,
        ), Rect(0, 0, 40, 100))

        self.assertEqual(tree.rect("first").as_tuple(), (0, 0, 40, 10))
        self.assertEqual(tree.rect("second").as_tuple(), (0, 45, 40, 10))
        self.assertEqual(tree.rect("third").as_tuple(), (0, 90, 40, 10))

    def test_auto_gap_remains_zero_when_flexible_children_consume_space(self):
        tree = Tree(Column(
            Fill(ThemeColor.PRIMARY).height(10).ref("first"),
            Spacer().ref("flex"),
            Fill(ThemeColor.WARNING).height(10).ref("last"),
            gap=None,
        ), Rect(0, 0, 40, 100))

        self.assertEqual(tree.rect("flex").as_tuple(), (0, 10, 40, 80))
        self.assertEqual(tree.rect("last").as_tuple(), (0, 90, 40, 10))

    def test_auto_gap_uses_compact_button_extent_without_changing_fixed_gap_lists(self):
        auto = Tree(Column(
            Button(test_action(TestCommand.ONE), "ONE").ref("one"),
            Button(test_action(TestCommand.TWO), "TWO").ref("two"),
            gap=None,
        ), Rect(0, 0, 200, 200))
        fixed = Tree(Column(
            Button(test_action(TestCommand.ONE), "ONE").ref("one"),
            Button(test_action(TestCommand.TWO), "TWO").ref("two"),
            gap=0,
        ), Rect(0, 0, 200, 200))

        self.assertEqual(auto.rect("one").height, 48)
        self.assertEqual(auto.rect("two").y, 152)
        self.assertEqual(fixed.rect("one").height, 100)
        self.assertEqual(fixed.rect("two").y, 100)

    def test_grid_arranges_a_typed_visual_matrix(self):
        tree = Tree(Grid(
            matrix=(
                (Fill(ThemeColor.PRIMARY).ref("fixed"), EMPTY),
                (EMPTY, Fill(ThemeColor.SECONDARY).ref("fill")),
            ),
            columns=(20, FLEX), rows=(10, FLEX), gap=(3, 4),
        ), Rect(5, 7, 100, 50))

        self.assertEqual(tree.rect("fixed").as_tuple(), (5, 7, 20, 10))
        self.assertEqual(tree.rect("fill").as_tuple(), (28, 21, 77, 36))

    def test_wrapped_auto_height_text_expands_and_moves_following_content(self):
        short = Tree(Column(
            Text("Short", wrap=True, auto_height=True).ref("text"),
            Fill(ThemeColor.SECONDARY).height(10).ref("after"),
            gap=2,
        ), Rect(0, 0, 120, 320))
        long = Tree(Column(
            Text(
                "A long briefing sentence that wraps across several lines "
                "and therefore needs an intrinsic vertical extent.",
                wrap=True, auto_height=True,
            ).ref("text"),
            Fill(ThemeColor.SECONDARY).height(10).ref("after"),
            gap=2,
        ), Rect(0, 0, 120, 320))

        self.assertGreater(long.rect("text").height, short.rect("text").height)
        self.assertGreater(long.rect("after").y, short.rect("after").y)
        self.assertEqual(long.rect("after").height, 10)

    def test_explicit_text_height_wins_over_intrinsic_height(self):
        tree = Tree(Column(
            Text("A " * 80, wrap=True, auto_height=True).height(24).ref("text"),
            Spacer(),
        ), Rect(0, 0, 120, 120))

        self.assertEqual(tree.rect("text").height, 24)

    def test_column_uses_element_sizes_instead_of_numeric_tuples(self):
        tree = Tree(Column(
            Fill(ThemeColor.PRIMARY).height(12).ref("top"),
            Spacer(),
            Fill(ThemeColor.SECONDARY).height(8).ref("bottom"),
            gap=2,
        ), Rect(10, 20, 50, 40))

        self.assertEqual(tree.rect("top").as_tuple(), (10, 20, 50, 12))
        self.assertEqual(tree.rect("bottom").as_tuple(), (10, 52, 50, 8))

    def test_overlay_child_offset_is_safe_and_layout_managed_children_reject_overflow(self):
        tree = Tree(Overlay(
            Fill(ThemeColor.PRIMARY).size(20, 10).align(
                horizontal="left", vertical="top").offset(4, 6).ref("leaf"),
        ), Rect(10, 20, 50, 40))

        self.assertEqual(tree.rect("leaf").as_tuple(), (14, 26, 20, 10))
        with self.assertRaisesRegex(ValueError, "moves it outside slot"):
            Tree(Overlay(
                Fill(ThemeColor.PRIMARY).size(20, 10).align(
                    horizontal="left", vertical="top").offset(-1, 0),
            ), Rect(10, 20, 50, 40))

    def test_shared_margin_padding_size_and_alignment_are_inherited(self):
        tree = Tree(Overlay(
            Fill(ThemeColor.PRIMARY).size(20, 10).margin(right=3, bottom=4)
            .align(horizontal="right", vertical="bottom").ref("leaf"),
            Column(
                Fill(ThemeColor.SECONDARY).height(5).ref("content"),
            ).padding(2).ref("container"),
        ), Rect(10, 20, 50, 40))

        self.assertEqual(tree.rect("leaf").as_tuple(), (37, 46, 20, 10))
        self.assertEqual(tree.rect("container").as_tuple(), (10, 20, 50, 40))
        self.assertEqual(tree.rect("content").as_tuple(), (12, 22, 46, 5))

    def test_wrap_panel_places_equal_items_without_manual_coordinates(self):
        tree = Tree(WrapPanel(
            Button(test_action(TestCommand.ONE), "ONE").ref("one"),
            Button(test_action(TestCommand.TWO), "TWO").ref("two"),
            Button(test_action(TestCommand.THREE), "THREE").ref("three"),
            item_width=30, item_height=12, horizontal_gap=5,
        ), Rect(0, 0, 100, 30))

        self.assertEqual(tree.rect("one").as_tuple(), (0, 0, 30, 12))
        self.assertEqual(tree.rect("two").as_tuple(), (35, 0, 30, 12))
        self.assertEqual(tree.rect("three").as_tuple(), (70, 0, 30, 12))

    def test_text_font_and_button_style_are_separate_overrides(self):
        content = Override(Column(
            Text("LABEL"),
            Button(test_action(), "BUTTON"),
        )).with_font("JetBrainsMono 16pt").with_button_style(
            ButtonStyle(font="JetBrainsMono 8pt")).apply()
        page = PageTree(content, Rect(0, 0, 100, 40), page_id=TestPage.LAYOUT)

        drawing = "\n".join(page.draw(FeatherRenderer()))

        self.assertIn('-f "JetBrainsMono 16pt"', drawing)
        self.assertIn('-f "JetBrainsMono 8pt"', drawing)

    def test_explicit_overflow_is_opt_in(self):
        tree = Tree(Overlay(
            Fill(ThemeColor.PRIMARY).height(11).allow_overflow().ref("overflow"),
        ), Rect(0, 0, 20, 10))

        self.assertEqual(tree.rect("overflow").as_tuple(), (0, 0, 20, 11))


class DirtyRenderingTest(unittest.TestCase):
    def test_declarative_page_sets_and_begin_page_resets_semantic_identity(self):
        page = PageTree(
            Text("IDENTIFIED"), Rect(0, 0, 100, 20),
            page_id=TestPage.LAYOUT)
        renderer = FeatherRenderer()

        renderer.begin_page("identified")
        self.assertIsNone(renderer.get_status()["semantic_page_id"])
        page.draw(renderer)
        self.assertEqual(
            renderer.get_status()["semantic_page_id"],
            page.page_id)
        renderer.begin_page("legacy")
        self.assertIsNone(renderer.get_status()["semantic_page_id"])

    def test_page_redraws_only_the_dirty_repaint_boundary(self):
        page = PageTree(Overlay(
            Overlay(
                Fill(ThemeColor.PRIMARY),
                Text(bind(TestState.LEFT)),
            ).ref("left").repaint_boundary(),
            Overlay(
                Fill(ThemeColor.SECONDARY),
                Text(bind(TestState.RIGHT)),
            ).width(40).align(horizontal="right")
            .ref("right").repaint_boundary(),
        ), Rect(0, 0, 100, 20), page_id=TestPage.LAYOUT)
        renderer = FeatherRenderer()

        page.draw(renderer, {TestState.LEFT: "A", TestState.RIGHT: "B"})
        self.assertEqual(page.update(renderer, {TestState.LEFT: "A"}), [])
        drawing = "\n".join(page.update(renderer, {TestState.LEFT: "C"}))

        self.assertIn(
            "-p 0 0 -s 100 20 -c %s" %
            renderer.color(ThemeColor.PRIMARY), drawing)
        self.assertIn('-t "C"', drawing)
        self.assertNotIn(
            renderer.color(ThemeColor.SECONDARY), drawing)
        self.assertNotIn('-t "B"', drawing)

    def test_component_can_be_invalidated_without_a_partial_tree(self):
        page = PageTree(Overlay(
            Fill(ThemeColor.PRIMARY).ref("surface"),
        ), Rect(0, 0, 20, 10), page_id=TestPage.LAYOUT)
        renderer = FeatherRenderer()
        page.draw(renderer)

        page.invalidate("surface")
        drawing = "\n".join(page.update(renderer))

        self.assertEqual(drawing.count("--batch fill"), 1)


class HeatLayoutTest(unittest.TestCase):
    MATERIALS = ("PLA", "PETG", "ABS", "ABS-PC", "TPU")

    def setUp(self):
        self.page = heat.get_page(self.MATERIALS)

    def assert_ordered_without_overlap(self, refs):
        rectangles = [self.page.rect(ref) for ref in refs]
        for previous, following in zip(rectangles, rectangles[1:]):
            self.assertLessEqual(previous.right, following.x)

    def test_fan_row_has_independent_non_overlapping_tracks(self):
        refs = (
            heat.HeatRef.FAN_LABEL, heat.HeatRef.FAN_VALUE,
            heat.HeatRef.FAN_0, heat.HeatRef.FAN_50,
            heat.HeatRef.FAN_100,
        )

        self.assert_ordered_without_overlap(refs)
        label = self.page.rect(heat.HeatRef.FAN_LABEL)
        self.assertGreaterEqual(
            label.width,
            FeatherRenderer.text_width("PART FAN", "JetBrainsMono 8pt"))
        for ref in refs:
            rectangle = self.page.rect(ref)
            self.assertGreaterEqual(rectangle.x, self.page.bounds.x)
            self.assertLessEqual(rectangle.right, self.page.bounds.right)

    def test_heater_rows_share_the_same_grid_geometry(self):
        nozzle = (
            heat.HeatRef.NOZZLE_LABEL, heat.HeatRef.NOZZLE_VALUE,
            heat.HeatRef.NOZZLE_MINUS, heat.HeatRef.NOZZLE_PLUS,
            heat.HeatRef.NOZZLE_OFF,
        )
        bed = (
            heat.HeatRef.BED_LABEL, heat.HeatRef.BED_VALUE,
            heat.HeatRef.BED_MINUS, heat.HeatRef.BED_PLUS,
            heat.HeatRef.BED_OFF,
        )

        self.assert_ordered_without_overlap(nozzle)
        self.assert_ordered_without_overlap(bed)
        self.assertEqual(
            [self.page.rect(ref).x for ref in nozzle],
            [self.page.rect(ref).x for ref in bed])
        self.assertLess(
            self.page.rect(heat.HeatRef.HEATERS).bottom,
            self.page.rect(heat.HeatRef.FAN_ROW).y)

    def test_five_presets_are_centered_inside_content(self):
        presets = [self.page.rect("heat.preset.%d" % index)
                   for index in range(5)]

        for previous, following in zip(presets, presets[1:]):
            self.assertLessEqual(previous.right, following.x)
        self.assertEqual(
            (presets[0].x + presets[-1].right) // 2,
            self.page.bounds.center[0])
        self.assertLess(
            self.page.rect(heat.HeatRef.PRESETS).bottom,
            self.page.rect(heat.HeatRef.COOLDOWN).y)

    def test_actions_are_typed_and_preserve_material_order(self):
        preheats = [
            action for action in self.page.actions.values()
            if action.key == heat.HeatCommand.PREHEAT
        ]

        self.assertEqual(
            [action.payload for action in preheats], list(self.MATERIALS))
        self.assertTrue(all(action.key.__class__ is heat.HeatCommand
                            for action in self.page.actions.values()))

    def test_heat_page_uses_context_roles_for_product_accents(self):
        renderer = FeatherRenderer()
        role_colors = {
            ThemeRole.TEMPERATURE_NOZZLE.value: "a1b2c3",
            ThemeRole.TEMPERATURE_BED.value: "b2c3d4",
            ThemeRole.TEMPERATURE_FAN.value: "c3d4e5",
        }
        renderer._palette = resolve_theme(FALLBACK_THEME, role_colors)

        drawing = "\n".join(self.page.draw(renderer))

        expected = {
            "NOZZLE": renderer.color(ThemeRole.TEMPERATURE_NOZZLE),
            "BED": renderer.color(ThemeRole.TEMPERATURE_BED),
            "PART FAN": renderer.color(ThemeRole.TEMPERATURE_FAN),
        }
        for label, color in expected.items():
            with self.subTest(label=label):
                line = next(
                    command for command in drawing.splitlines()
                    if '-t "%s"' % label in command)
                self.assertIn("-c %s" % color, line)

    def test_telemetry_update_redraws_only_changed_value_boundary(self):
        renderer = FeatherRenderer()
        initial = {
            heat.HeatState.NOZZLE: 21.5,
            heat.HeatState.NOZZLE_TARGET: 220.0,
            heat.HeatState.BED: 24.0,
            heat.HeatState.BED_TARGET: 60.0,
            heat.HeatState.FAN: 25.0,
            heat.HeatState.FAN_AVAILABLE: True,
        }
        self.page.draw(renderer, initial)
        drawing = "\n".join(self.page.update(renderer, {
            heat.HeatState.FAN: 50.0,
        }))

        fan = self.page.rect(heat.HeatRef.FAN_VALUE)
        self.assertIn(
            "-p %d %d -s %d %d" % fan.as_tuple(), drawing)
        self.assertIn('-t "50%"', drawing)


class FilamentLayoutTest(unittest.TestCase):
    PROFILES = (("PLA", 220.0), ("PETG", 250.0), ("ABS", 260.0),
                ("ABS-PC", 270.0), ("TPU", 220.0))

    def test_material_grid_is_centered_typed_and_omits_repeated_nozzle(self):
        page = filament.get_material_page(self.PROFILES)
        renderer = FeatherRenderer()
        page.draw(renderer, {})
        actions = [
            action for action in page.actions.values()
            if action.key == filament.FilamentCommand.SELECT]

        self.assertEqual([action.payload for action in actions],
                         [item[0] for item in self.PROFILES])
        first_row = [page.rect("filament.material.%d" % index)
                     for index in range(3)]
        second_row = [page.rect("filament.material.%d" % index)
                      for index in range(3, 5)]
        self.assertEqual((first_row[0].x + first_row[-1].right) // 2,
                         page.bounds.center_x)
        self.assertEqual((second_row[0].x + second_row[-1].right) // 2,
                         page.bounds.center_x)

    def test_action_status_repaints_as_one_boundary(self):
        page = filament.get_action_page(False)
        renderer = FeatherRenderer()
        page.draw(renderer, {
            filament.FilamentState.TEMPERATURE: 130.4,
            filament.FilamentState.TARGET: 250.0,
            filament.FilamentState.MATERIAL: "PETG",
            filament.FilamentState.READY: False,
            filament.FilamentState.COOLING: False,
        })

        drawing = "\n".join(page.update(renderer, {
            filament.FilamentState.TEMPERATURE: 131.2,
        }))
        status = page.rect("filament.action.status")

        self.assertIn("-p %d %d -s %d %d" % status.as_tuple(), drawing)
        page.update(renderer, {
            filament.FilamentState.TEMPERATURE: 260.0,
            filament.FilamentState.COOLING: True,
        })
        page.update(renderer, {
            filament.FilamentState.TEMPERATURE: 250.0,
            filament.FilamentState.READY: True,
            filament.FilamentState.COOLING: False,
        })

    def test_action_cards_form_a_non_overlapping_vertical_workflow(self):
        page = filament.get_action_page(True)
        refs = ("filament.action.load", "filament.action.unload",
                "filament.action.purge", "filament.action.finish")
        rectangles = [page.rect(ref) for ref in refs]

        for previous, following in zip(rectangles, rectangles[1:]):
            self.assertLessEqual(previous.bottom, following.y)
        self.assertEqual(page.rect("filament.action.status").height, 348)
        self.assertLessEqual(rectangles[-1].bottom, page.bounds.bottom)


class MovementLayoutTest(unittest.TestCase):
    def test_post_home_park_position_is_valid_telemetry(self):
        renderer = FeatherRenderer()
        values = move.snapshot_values(
            (120.0, 120.0, 230.0, "HOMED: XYZ", True, True))

        drawing = "\n".join(move.render_step(renderer, values))

        self.assertIn("X  120.00   Y  120.00", drawing)
        self.assertIn("Z  230.00", drawing)

    def test_joystick_columns_and_controls_derive_from_page_tree(self):
        page = move.JOYSTICK_PAGE

        self.assertEqual(page.rect("xy.panel").as_tuple(), (12, 64, 456, 364))
        self.assertEqual(page.rect("z.panel").as_tuple(), (478, 64, 100, 364))
        self.assertEqual(
            page.rect("status.panel").as_tuple(), (588, 64, 200, 364))
        self.assertEqual(page.rect("xy.pad").as_tuple(), (30, 96, 420, 266))
        self.assertEqual(page.rect("z.hitbox").as_tuple(), (486, 96, 84, 329))
        self.assertEqual(page.rect("z.track").bottom,
                         page.rect("xy.actions").bottom)
        self.assertLessEqual(page.rect("z.hitbox").bottom,
                             move.MOVE_CONTENT.bottom)
        self.assertEqual(move_geometry.JOYSTICK_XY_CENTER,
                         page.rect("xy.pad").center)
        self.assertEqual(move_geometry.JOYSTICK_Z_CENTER,
                         page.rect("z.track").center)
        self.assertEqual(move_geometry.JOYSTICK_Z_RADIUS,
                         (page.rect("z.track").height - 3) // 2)

    def test_step_grid_keeps_axis_buttons_symmetric(self):
        page = move.STEP_PAGE

        self.assertEqual(page.rect("xy.up").x, page.rect("xy.down").x)
        self.assertEqual(page.rect("xy.left").y, page.rect("xy.right").y)
        self.assertEqual(page.rect("xy.status").center, (190, 192))
        self.assertEqual(page.rect("z.status").center, (397, 192))
        self.assertEqual(page.rect("preset.2").right,
                         page.rect("control").right)

    def test_vertical_scale_is_rendered_from_arranged_track_geometry(self):
        renderer = FeatherRenderer()
        values = move.snapshot_values(
            (0.0, 0.0, 0.0, "HOMED: XYZ", True, True))
        values[move.MoveState.INERTIA] = 0.0
        values[move.MoveState.CURSOR] = None
        drawing = "\n".join(move.render_joystick(renderer, values))
        track = move.JOYSTICK_PAGE.rect("z.track")

        self.assertIn("-p 509 103 -s 12 1", drawing)
        self.assertIn("-p 509 260 -s 12 1", drawing)
        self.assertIn("-p 509 417 -s 12 1", drawing)
        self.assertIn("-p 513 182 -s 8 1", drawing)
        self.assertIn("-p 516 142 -s 5 1", drawing)
        self.assertEqual(track.as_tuple(), (541, 103, 10, 315))

    def test_dirty_state_replaces_all_movement_partial_trees(self):
        self.assertFalse(hasattr(move, "STEP_STATUS_TREE"))
        self.assertFalse(hasattr(move, "JOYSTICK_POSITION_TREE"))
        self.assertFalse(hasattr(move, "STEP_MOVE_LAYOUT"))
        self.assertFalse(hasattr(move, "JOYSTICK_MOVE_LAYOUT"))

        renderer = FeatherRenderer()
        initial = move.snapshot_values(
            (0.0, 0.0, 0.0, "HOMED: XYZ", True, True))
        initial.update({
            move.MoveState.INERTIA: 0.0,
            move.MoveState.CURSOR: None,
        })
        move.render_joystick(renderer, initial)
        updated = move.snapshot_values(
            (1.0, 2.0, 3.0, "HOMED: XYZ", True, True))
        updated.update({
            move.MoveState.INERTIA: 0.0,
            move.MoveState.CURSOR: None,
        })
        drawing = "\n".join(move.update_joystick(renderer, updated))

        self.assertIn("-p 602 96 -s 172 92", drawing)

    def test_movement_screen_is_a_nested_object_tree(self):
        self.assertIsInstance(move.STEP_PAGE.root, object)
        self.assertIn("xy.grid", move.STEP_PAGE.layout.keys())
        self.assertIn("presets", move.STEP_PAGE.layout.keys())
        self.assertIn("xy.pad", move.JOYSTICK_PAGE.layout.keys())
        self.assertIn("home.buttons", move.JOYSTICK_PAGE.layout.keys())

    def test_movement_primary_columns_are_weighted_grid_tracks(self):
        step_root = move.STEP_PAGE.root
        joystick_root = move.JOYSTICK_PAGE.root

        self.assertIsInstance(step_root, Grid)
        self.assertEqual(
            [track.weight if isinstance(track, Flex) else track
             for track in step_root.columns],
            [400, 35, 305])
        self.assertIsInstance(joystick_root, Grid)
        self.assertEqual(
            [track.weight for track in joystick_root.columns],
            [456, 100, 200])


class ZOffsetLayoutTest(unittest.TestCase):
    def test_safe_z_briefing_text_has_sufficient_non_overlapping_layout(self):
        page = z_offset.SAFE_BRIEFING_PAGE
        static_text = [
            node for node in page.root.walk()
            if isinstance(node, Text) and isinstance(node.value, str)
        ]
        rectangles = [page.layout.rect(node) for node in static_text]

        self.assertEqual(len(rectangles), 4)
        for node, rectangle in zip(static_text, rectangles):
            self.assertGreater(rectangle.height, 0)
            self.assertGreaterEqual(
                rectangle.height,
                node.preferred_extent("vertical", rectangle.width))
        for previous, following in zip(rectangles, rectangles[1:]):
            self.assertLessEqual(previous.bottom, following.y)

        commands = z_offset.render_safe_briefing(FeatherRenderer(), {
            z_offset.SafeBriefingState.CURRENT: 10.0,
            z_offset.SafeBriefingState.START: 20.0,
        })
        text_commands = [command for command in commands
                         if command.startswith("--batch text")]
        for node in static_text:
            self.assertTrue(any(str(node.value) in command
                                for command in text_commands))

    def test_complete_z_offset_flow_uses_declarative_pages(self):
        self.assertEqual(
            z_offset.SUMMARY_PAGE.rect("summary.save").as_tuple(),
            (65, 334, 670, 82))
        self.assertEqual(
            z_offset.PAPER_PAGE.rect("paper.gauge").as_tuple(),
            (710, 72, 70, 358))
        self.assertEqual(
            z_offset.PAPER_PAGE.rect("paper.probe").as_tuple(),
            (20, 154, 325, 70))
        self.assertEqual(
            z_offset.PAPER_PAGE.rect("paper.accept").as_tuple(),
            (245, 380, 445, 48))

    def test_z_offset_button_groups_use_layout_gaps_not_child_margins(self):
        closer = z_offset.PAPER_PAGE.layout.node("paper.closer")
        farther = z_offset.PAPER_PAGE.layout.node("paper.farther")

        self.assertIs(closer.parent, farther.parent)
        self.assertIsInstance(closer.parent, Grid)
        self.assertEqual(closer.parent.column_gap, 20)
        self.assertEqual(closer.layout_options.margin.horizontal, 0)
        self.assertEqual(farther.layout_options.margin.horizontal, 0)

    def test_force_gauge_updates_through_dirty_page_tree(self):
        renderer = FeatherRenderer()
        state = {
            z_offset.PaperState.MANUAL: False,
            z_offset.PaperState.REFERENCE: "--",
            z_offset.PaperState.NOZZLE: "--",
            z_offset.PaperState.CANDIDATE: "--",
            z_offset.PaperState.PROBING: False,
            z_offset.PaperState.MOVING_TO_START: False,
            z_offset.PaperState.STEP: 0.010,
            z_offset.PaperState.READY: False,
            z_offset.PaperState.GAUGE: None,
            z_offset.PaperState.DIALOG: None,
            z_offset.PaperState.DIALOG_WEIGHT: 0.0,
        }
        z_offset.render_paper(renderer, state)

        drawing = "\n".join(z_offset.update_paper_gauge(renderer, {
            "initial": 100.0, "minimum": 80.0,
            "maximum": 120.0, "value": 110.0,
        }))

        self.assertIn("-p 710 72 -s 70 358", drawing)
        self.assertIn('-t "+110.0"', drawing)
        self.assertNotIn("z.probe", drawing)

    def test_z_offset_dialogs_remain_modal(self):
        renderer = FeatherRenderer()
        summary = {
            z_offset.SummaryState.ZONE_LABELS: dict(
                (key, key.upper()) for key in z_offset.ZONE_ACTIONS),
            z_offset.SummaryState.RESULTS: {},
            z_offset.SummaryState.SPREAD: 0.0,
            z_offset.SummaryState.POSITIONAL_WARNING: 0.025,
            z_offset.SummaryState.SELECTED: None,
            z_offset.SummaryState.AVERAGE: None,
            z_offset.SummaryState.LOAD_ZOFFSET: False,
            z_offset.SummaryState.DIALOG: "discard",
        }
        drawing = "\n".join(z_offset.render_summary(renderer, summary))

        self.assertIn("--batch clear-hitboxes", drawing)
        self.assertIn("z.discard.confirm", drawing)

    def test_z_calibration_mixin_contains_no_coordinate_draw_calls(self):
        source = (PLUGINS / "feather_z_calibration.py").read_text(
            encoding="utf-8")

        self.assertNotIn("self.renderer.button(", source)
        self.assertNotIn("self.renderer.panel(", source)
        self.assertNotIn("self.renderer.text(", source)
        self.assertIn("z_offset_ui.render_paper", source)


if __name__ == "__main__":
    unittest.main()
