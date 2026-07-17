## Tests for Feather joystick motion planning.
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).parents[1]
MODULE = ROOT / ".py" / "klipper" / "plugins" / "feather_joystick.py"
spec = importlib.util.spec_from_file_location("feather_joystick", MODULE)
JOYSTICK = importlib.util.module_from_spec(spec)
spec.loader.exec_module(JOYSTICK)


class JoystickPlannerTest(unittest.TestCase):
    def planner(self):
        return JOYSTICK.JoystickPlanner(
            600.0, 10000.0, 25.0, 250.0,
            ((-110.0, 110.0), (-110.0, 110.0), (0.0, 220.0)))

    def test_xy_dead_zone_direction_and_radial_clamp(self):
        self.assertEqual(JOYSTICK.radial_input(200, 200, 200, 200, 100),
                         (0.0, 0.0))
        right = JOYSTICK.radial_input(300, 200, 200, 200, 100)
        up = JOYSTICK.radial_input(200, 100, 200, 200, 100)
        diagonal = JOYSTICK.radial_input(400, 0, 200, 200, 100)
        self.assertAlmostEqual(right[0], 1.0)
        self.assertAlmostEqual(up[1], 1.0)
        self.assertAlmostEqual((diagonal[0] ** 2 + diagonal[1] ** 2) ** 0.5,
                               1.0)

    def test_z_control_is_inverted_for_bed_coordinates(self):
        self.assertLess(JOYSTICK.vertical_input(100, 200, 100), 0.0)
        self.assertGreater(JOYSTICK.vertical_input(300, 200, 100), 0.0)
        self.assertEqual(JOYSTICK.vertical_input(205, 200, 100), 0.0)

    def test_velocity_changes_are_limited_by_half_configured_acceleration(self):
        planner = self.planner()
        planner.set_xy(300, 200, 0.0, 200, 200, 100)
        segment = planner.advance((0.0, 0.0, 100.0), 0.05)
        self.assertIsNotNone(segment)
        self.assertAlmostEqual(planner.velocity[0], 500.0)
        self.assertAlmostEqual(segment.acceleration, 10000.0)
        planner.release()
        planner.advance(segment.position, 0.05)
        self.assertAlmostEqual(planner.velocity[0], 0.0)

    def test_z_uses_its_own_speed_and_acceleration_limits(self):
        planner = self.planner()
        planner.set_z(300, 0.0, 200, 100)
        segment = planner.advance((0.0, 0.0, 100.0), 0.05)
        self.assertAlmostEqual(planner.velocity[2], 12.5)
        self.assertAlmostEqual(segment.acceleration, 250.0)
        planner.advance(segment.position, 0.05)
        self.assertAlmostEqual(planner.velocity[2], 25.0)

    def test_switching_levers_never_creates_mixed_xyz_segment(self):
        planner = self.planner()
        planner.set_xy(300, 200, 0.0, 200, 200, 100)
        position = (0.0, 0.0, 100.0)
        first = planner.advance(position, 0.05)
        self.assertGreater(first.position[0], position[0])

        planner.set_z(300, 0.05, 200, 100)
        braking = planner.advance(first.position, 0.05)
        self.assertEqual(braking.position[2], first.position[2])
        self.assertAlmostEqual(planner.velocity[0], 0.0)

        z_motion = planner.advance(braking.position, 0.05)
        self.assertEqual(z_motion.position[0], braking.position[0])
        self.assertGreater(z_motion.position[2], braking.position[2])

    def test_braking_keeps_segments_inside_move_safe_boundaries(self):
        planner = self.planner()
        planner.set_xy(300, 200, 0.0, 200, 200, 100)
        position = [0.0, 0.0, 100.0]
        velocities = []
        for _index in range(100):
            segment = planner.advance(position, 0.05)
            if segment is None:
                break
            position = segment.position
            velocities.append(planner.velocity[0])
        self.assertLessEqual(position[0], 109.5)
        self.assertTrue(any(value < 590.0 for value in velocities[-8:]))
        self.assertEqual(planner.velocity[0], 0.0)
        # The last segment must decelerate naturally rather than reaching the
        # boundary with speed and relying on the emergency position clamp.
        self.assertGreater(len(velocities), 5)
        self.assertTrue(all(velocities[index] <= velocities[index - 1]
                            for index in range(velocities.index(max(velocities)) + 1,
                                               len(velocities))))

    def test_diagonal_braking_is_safe_at_a_corner(self):
        planner = self.planner()
        planner.set_xy(300, 100, 0.0, 200, 200, 100)
        position = [0.0, 0.0, 100.0]
        for _index in range(100):
            segment = planner.advance(position, 0.05)
            if segment is None:
                break
            position = segment.position
        self.assertLessEqual(position[0], 109.5)
        self.assertLessEqual(position[1], 109.5)
        self.assertAlmostEqual(planner.velocity[0], 0.0)
        self.assertAlmostEqual(planner.velocity[1], 0.0)

    def test_z_brakes_before_both_inverted_control_limits(self):
        for touch_y, start_z, expected_limit in (
                (300, 100.0, 219.5), (100, 100.0, 0.5)):
            planner = self.planner()
            planner.set_z(touch_y, 0.0, 200, 100)
            position = [0.0, 0.0, start_z]
            for _index in range(400):
                segment = planner.advance(position, 0.05)
                if segment is None:
                    break
                position = segment.position
            self.assertAlmostEqual(position[2], expected_limit, places=5)
            self.assertAlmostEqual(planner.velocity[2], 0.0)

    def test_lost_touch_heartbeat_releases_control(self):
        planner = self.planner()
        planner.set_xy(300, 200, 10.0, 200, 200, 100)
        self.assertFalse(planner.watchdog(10.3))
        self.assertTrue(planner.watchdog(10.36))
        self.assertFalse(planner.held)
        self.assertEqual(planner.target, [0.0, 0.0, 0.0])


if __name__ == "__main__":
    unittest.main()
