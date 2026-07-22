## Tests for portable semantic actions and routing.
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from dataclasses import dataclass
from enum import Enum
import pathlib
import sys
import unittest


PLUGINS = (pathlib.Path(__file__).parents[1] / ".py" / "klipper" /
           "plugins")
sys.path.insert(0, str(PLUGINS))

from ui import (  # noqa: E402
    Back, Button, Command, CommandKey, Dialog, Hitbox, HomingHint,
    Increment, JoystickKnob, Navigate, PageKey, PageTree, Rect, Replace,
    Router, SetValue, StateKey, Toggle, action_metadata,
    reflect_page, state,
)


class TestPage(PageKey):
    HOME = "test.home"
    DETAILS = "test.details"


class TestState(StateKey):
    COUNT = state(int, default=1, minimum=0, maximum=3)
    FLAG = state(bool, default=False)
    MODE = state(str, default="one", choices=("one", "two", "three"))
    READ_ONLY = state(str, default="fixed", mutable=False)


class TestCommand(CommandKey):
    HOME = "test.command.home"
    CUSTOM = "test.command.custom"


class Axis(Enum):
    X = "x"
    Y = "y"


@dataclass(frozen=True)
class HomeRequest:
    axes: tuple


@dataclass(frozen=True)
class CustomRequest:
    value: int


@dataclass
class MutableRequest:
    value: int


HOME_COMMAND = Command(
    TestCommand.HOME, HomeRequest((Axis.X, Axis.Y)),
    hint=HomingHint((Axis.X, Axis.Y), (Axis.X, Axis.Y)))


def page(page_key, action):
    return PageTree(
        Button(action, "ACTION"), Rect(0, 0, 100, 40), page_id=page_key,
        state_schema=(TestState.COUNT, TestState.FLAG, TestState.MODE))


class SemanticActionContractTest(unittest.TestCase):
    def test_interactive_components_require_semantic_actions(self):
        with self.assertRaisesRegex(TypeError, "Button action"):
            Button("test.action", "ACTION")
        with self.assertRaisesRegex(TypeError, "Hitbox action"):
            Hitbox("test.action")
        with self.assertRaisesRegex(TypeError, "Dialog button action"):
            Dialog("TITLE", (), (("test.action", "OK", "enabled"),))
        with self.assertRaisesRegex(TypeError, "active_action"):
            JoystickKnob(active_action="test.action")

    def test_state_effects_require_mutable_typed_keys(self):
        with self.assertRaisesRegex(TypeError, "StateKey"):
            SetValue("count", 2)
        with self.assertRaisesRegex(ValueError, "mutable"):
            SetValue(TestState.READ_ONLY, "other")
        with self.assertRaisesRegex(ValueError, "mutable"):
            Toggle(TestState.READ_ONLY)
        with self.assertRaisesRegex(ValueError, "mutable"):
            Increment(TestState.READ_ONLY)
        with self.assertRaisesRegex(TypeError, "boolean"):
            Toggle(TestState.COUNT)

    def test_command_metadata_is_portable_and_typed(self):
        metadata = action_metadata(HOME_COMMAND)

        self.assertEqual(metadata["kind"], "command")
        self.assertTrue(metadata["wire_id"].startswith("test.command.home."))
        self.assertTrue(metadata["key"].endswith("TestCommand.HOME"))
        self.assertTrue(metadata["payload"]["axes"][0].endswith("Axis.X"))
        self.assertTrue(metadata["payload"]["axes"][1].endswith("Axis.Y"))
        self.assertEqual(metadata["hint"]["kind"], "homing")
        self.assertTrue(metadata["hint"]["sequence"][0].endswith("Axis.X"))
        self.assertTrue(metadata["hint"]["sequence"][1].endswith("Axis.Y"))

    def test_command_rejects_nonportable_payloads(self):
        with self.assertRaisesRegex(TypeError, "immutable typed value"):
            Command(TestCommand.CUSTOM, object())
        with self.assertRaisesRegex(TypeError, "must be frozen"):
            Command(TestCommand.CUSTOM, MutableRequest(1))
        with self.assertRaisesRegex(TypeError, "immutable typed value"):
            Command(TestCommand.CUSTOM, [1, 2])

    def test_command_wire_identity_includes_typed_payload(self):
        first = Command(TestCommand.CUSTOM, CustomRequest(1))
        second = Command(TestCommand.CUSTOM, CustomRequest(2))
        page = PageTree(
            Dialog("TITLE", (), (
                (first, "ONE", "enabled"),
                (second, "TWO", "enabled"),
            )), Rect(0, 0, 100, 80), page_id=TestPage.HOME)

        self.assertNotEqual(first.wire_id, second.wire_id)
        self.assertEqual(page.resolve_action(first.wire_id), first)
        self.assertEqual(page.resolve_action(second.wire_id), second)



class RouterTest(unittest.TestCase):
    def setUp(self):
        self.home = page(TestPage.HOME, Navigate(TestPage.DETAILS))
        self.details = page(TestPage.DETAILS, Back())
        self.router = Router((self.home, self.details), current=TestPage.HOME)

    def test_navigation_back_and_replace_use_typed_page_keys(self):
        result = self.router.dispatch(
            Navigate(TestPage.DETAILS), self.home.initial_state())
        self.assertEqual(result.page, TestPage.DETAILS)
        self.assertTrue(result.history_changed)
        self.assertEqual(self.router.history, [TestPage.HOME])

        result = self.router.dispatch(Back(), self.details.initial_state())
        self.assertEqual(result.page, TestPage.HOME)
        self.assertTrue(result.history_changed)

        result = self.router.dispatch(
            Replace(TestPage.DETAILS), self.home.initial_state())
        self.assertEqual(result.page, TestPage.DETAILS)
        self.assertEqual(self.router.history, [])

    def test_state_effects_are_validated_and_transactional(self):
        store = self.home.initial_state()
        result = self.router.dispatch(SetValue(TestState.COUNT, 3), store)
        self.assertEqual(result.state[TestState.COUNT], 3)
        self.assertEqual(store[TestState.COUNT], 1)

        result = self.router.dispatch(Toggle(TestState.FLAG), result.state)
        self.assertTrue(result.state[TestState.FLAG])

        result = self.router.dispatch(
            Increment(TestState.MODE, 1), result.state)
        self.assertEqual(result.state[TestState.MODE], "two")
        result = self.router.dispatch(
            Increment(TestState.MODE, 5, wrap=True), result.state)
        self.assertEqual(result.state[TestState.MODE], "one")

    def test_commands_are_returned_without_fabricated_behavior(self):
        result = self.router.dispatch(HOME_COMMAND, self.home.initial_state())
        self.assertEqual(result.command, HOME_COMMAND)
        self.assertEqual(result.page, TestPage.HOME)

    def test_reflection_exposes_real_page_actions(self):
        model = reflect_page(self.home)
        actions = dict((item["wire_id"], item) for item in model["actions"])

        wire_id = Navigate(TestPage.DETAILS).wire_id
        self.assertEqual(actions[wire_id]["kind"], "navigate")
        self.assertTrue(actions[wire_id]["target"].endswith(
            "TestPage.DETAILS"))


if __name__ == "__main__":
    unittest.main()
