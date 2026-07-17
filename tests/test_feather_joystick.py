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

    def test_xy_diagonal_updates_both_axes_in_the_same_segment(self):
        planner = self.planner()
        planner.set_xy(300, 100, 0.0, 200, 200, 100)
        segment = planner.advance((0.0, 0.0, 100.0))
        self.assertGreater(segment.position[0], 0.0)
        self.assertGreater(segment.position[1], 0.0)
        self.assertGreater(planner.velocity[0], 0.0)
        self.assertGreater(planner.velocity[1], 0.0)

    def test_realtime_loop_uses_fixed_short_segments(self):
        self.assertEqual(JOYSTICK.PERIOD, 0.010)
        self.assertEqual(JOYSTICK.FEEDBACK_PERIOD, 0.05)
        self.assertEqual(JOYSTICK.MAX_SPEED_SCALE, 0.5)

    def test_z_control_is_inverted_for_bed_coordinates(self):
        self.assertLess(JOYSTICK.vertical_input(100, 200, 100), 0.0)
        self.assertGreater(JOYSTICK.vertical_input(300, 200, 100), 0.0)
        self.assertEqual(JOYSTICK.vertical_input(205, 200, 100), 0.0)

    def test_full_force_builds_velocity_and_acceleration_gradually(self):
        planner = self.planner()
        planner.set_xy(300, 200, 0.0, 200, 200, 100)
        position = (0.0, 0.0, 100.0)
        velocities = []
        accelerations = []
        for _index in range(10):
            segment = planner.advance(position, 0.01)
            position = segment.position
            velocities.append(planner.velocity[0])
            accelerations.append(planner.acceleration[0])

        self.assertTrue(all(
            velocities[index] > velocities[index - 1]
            for index in range(1, len(velocities))))
        self.assertTrue(all(
            accelerations[index] >= accelerations[index - 1]
            for index in range(1, 5)))
        self.assertLess(velocities[0], velocities[-1])
        self.assertLessEqual(max(accelerations), planner.xy_force)
        self.assertLess(max(accelerations), planner.xy_accel)

    def test_z_uses_its_own_force_drag_and_speed_limits(self):
        planner = self.planner()
        planner.set_z(300, 0.0, 200, 100)
        position = (0.0, 0.0, 100.0)
        first = planner.advance(position, 0.01)
        self.assertGreater(planner.velocity[2], 0.0)
        self.assertLess(planner.velocity[2], planner.z_speed)
        self.assertLessEqual(abs(planner.acceleration[2]), planner.z_force)
        self.assertEqual(first.acceleration, planner.z_accel)
        position = first.position
        for _index in range(200):
            segment = planner.advance(position, 0.01)
            position = segment.position if segment is not None else position
        self.assertLessEqual(planner.velocity[2], planner.z_speed)
        self.assertGreater(planner.velocity[2], planner.z_speed * 0.95)

    def test_switching_levers_never_creates_mixed_xyz_segment(self):
        planner = self.planner()
        planner.set_xy(300, 200, 0.0, 200, 200, 100)
        position = (0.0, 0.0, 100.0)
        first = planner.advance(position, 0.05)
        self.assertGreater(first.position[0], position[0])

        planner.set_z(300, 0.05, 200, 100)
        braking = planner.advance(first.position, 0.05)
        self.assertEqual(braking.position[2], first.position[2])
        self.assertGreater(planner.velocity[0], 0.0)

        position = braking.position
        for _index in range(20):
            braking = planner.advance(position, 0.05)
            if braking is None:
                break
            position = braking.position
            if abs(planner.velocity[0]) <= JOYSTICK.VELOCITY_EPSILON:
                break
            self.assertEqual(braking.position[2], first.position[2])
        z_motion = planner.advance(position, 0.05)
        self.assertEqual(z_motion.position[0], braking.position[0])
        self.assertGreater(z_motion.position[2], braking.position[2])

    def test_xy_does_not_clamp_inactive_axes_after_homing(self):
        planner = self.planner()
        planner.set_xy(100, 200, 0.0, 200, 200, 100)
        position = (110.01, 110.04, 220.0)

        segment = planner.advance(position, 0.025)

        self.assertLess(segment.position[0], position[0])
        self.assertEqual(segment.position[1], position[1])
        self.assertEqual(segment.position[2], position[2])
        self.assertEqual(segment.acceleration, planner.xy_accel)

    def test_half_stick_applies_half_force_and_half_terminal_speed(self):
        full = self.planner()
        half = self.planner()
        full.set_xy(300, 200, 0.0, 200, 200, 100)
        # radial_input maps this point to approximately 50% after dead-zone.
        half.set_xy(256, 200, 0.0, 200, 200, 100)
        full_position = [0.0, 0.0, 100.0]
        half_position = [0.0, 0.0, 100.0]
        for _index in range(30):
            full_segment = full.advance(full_position, 0.01)
            half_segment = half.advance(half_position, 0.01)
            full_position = full_segment.position
            half_position = half_segment.position

        self.assertGreater(full.velocity[0], half.velocity[0])
        self.assertAlmostEqual(
            half.velocity[0] / full.velocity[0], 0.5, delta=0.08)

    def test_release_uses_smooth_fast_braking_without_reversing(self):
        planner = self.planner()
        planner.set_xy(300, 200, 0.0, 200, 200, 100)
        position = [0.0, 0.0, 100.0]
        for _index in range(30):
            segment = planner.advance(position, 0.01)
            position = segment.position
        release_speed = planner.velocity[0]
        planner.release()
        speeds = []
        for _index in range(30):
            segment = planner.advance(position, 0.01)
            position = segment.position if segment is not None else position
            speeds.append(planner.velocity[0])
            if segment is None:
                break

        self.assertGreater(speeds[0], release_speed)
        self.assertTrue(all(value >= 0.0 for value in speeds))
        self.assertAlmostEqual(speeds[-1], 0.0)
        self.assertLessEqual(len(speeds), 22)

    def test_release_after_xy_turn_brakes_straight_without_orbiting(self):
        planner = self.planner()
        position = [0.0, 0.0, 100.0]
        planner.set_xy(300, 200, 0.0, 200, 200, 100)
        for _index in range(30):
            position = planner.advance(position, 0.01).position
        planner.set_xy(200, 100, 0.3, 200, 200, 100)
        for _index in range(30):
            position = planner.advance(position, 0.01).position

        release_velocity = tuple(planner.velocity[:2])
        release_position = tuple(position[:2])
        planner.release()
        headings = []
        for _index in range(60):
            segment = planner.advance(position, 0.01)
            if segment is None:
                break
            position = segment.position
            velocity = tuple(planner.velocity[:2])
            if JOYSTICK._magnitude(velocity) > JOYSTICK.VELOCITY_EPSILON:
                cross = (release_velocity[0] * velocity[1]
                         - release_velocity[1] * velocity[0])
                headings.append(cross)

        self.assertTrue(headings)
        self.assertTrue(all(abs(value) < 0.000001 for value in headings))
        displacement = (
            position[0] - release_position[0],
            position[1] - release_position[1],
        )
        self.assertAlmostEqual(
            release_velocity[0] * displacement[1]
            - release_velocity[1] * displacement[0], 0.0, places=5)
        self.assertEqual(planner.velocity[:2], [0.0, 0.0])

    def test_orthogonal_force_never_reverses_uncommanded_axis(self):
        planner = self.planner()
        position = [-80.0, -80.0, 100.0]
        planner.set_xy(300, 200, 0.0, 200, 200, 100)
        for _index in range(30):
            position = planner.advance(position, 0.01).position

        planner.set_xy(200, 100, 0.3, 200, 200, 100)
        x_velocities = []
        for _index in range(100):
            segment = planner.advance(position, 0.01)
            if segment is None:
                break
            position = segment.position
            x_velocities.append(planner.velocity[0])

        self.assertTrue(x_velocities)
        self.assertTrue(all(value >= 0.0 for value in x_velocities))
        self.assertAlmostEqual(x_velocities[-1], 0.0)

    def test_reverse_force_brakes_before_changing_direction(self):
        planner = self.planner()
        planner.set_xy(300, 200, 0.0, 200, 200, 100)
        position = [0.0, 0.0, 100.0]
        for _index in range(25):
            segment = planner.advance(position, 0.01)
            position = segment.position
        initial_speed = planner.velocity[0]
        initial_acceleration = planner.acceleration[0]

        planner.set_xy(100, 200, 0.25, 200, 200, 100)
        first = planner.advance(position, 0.01)
        position = first.position
        self.assertGreater(planner.velocity[0], 0.0)
        self.assertLessEqual(
            abs(planner.acceleration[0] - initial_acceleration),
            planner.xy_jerk * 0.01 + 0.01)
        for _index in range(60):
            segment = planner.advance(position, 0.01)
            position = segment.position if segment is not None else position
            if planner.velocity[0] < 0.0:
                break
        self.assertGreater(initial_speed, 0.0)
        self.assertLess(planner.velocity[0], 0.0)

    def test_inertia_reports_velocity_and_acceleration(self):
        planner = self.planner()
        planner.set_xy(300, 100, 0.0, 200, 200, 100)
        planner.advance((0.0, 0.0, 100.0), 0.01)

        inertia = planner.inertia()

        self.assertGreater(inertia["xy_speed"], 0.0)
        self.assertGreater(inertia["velocity"][0], 0.0)
        self.assertGreater(inertia["velocity"][1], 0.0)
        self.assertGreater(inertia["acceleration_magnitude"], 0.0)

    def test_braking_keeps_segments_inside_move_safe_boundaries(self):
        planner = self.planner()
        planner.set_xy(300, 200, 0.0, 200, 200, 100)
        position = [0.0, 0.0, 100.0]
        velocities = []
        positions = []
        for _index in range(100):
            segment = planner.advance(position, 0.05)
            if segment is None:
                break
            position = segment.position
            velocities.append(planner.velocity[0])
            positions.append(position[0])
        self.assertLessEqual(position[0], 109.5)
        self.assertTrue(any(value < 590.0 for value in velocities[-8:]))
        self.assertEqual(planner.velocity[0], 0.0)
        self.assertTrue(all(
            positions[index] >= positions[index - 1]
            for index in range(1, len(positions))))
        for _index in range(20):
            self.assertIsNone(planner.advance(position, 0.05))
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
                (300, 100.0, 219.5), (100, 100.0, 0.0)):
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
        self.assertFalse(planner.watchdog(10.14))
        self.assertTrue(planner.watchdog(10.16))
        self.assertFalse(planner.held)
        self.assertEqual(planner.target, [0.0, 0.0, 0.0])


if __name__ == "__main__":
    unittest.main()
