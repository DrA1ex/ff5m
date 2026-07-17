## Low-allocation motion planning helpers for Feather's joystick controls.
##
## This module knows nothing about Klipper or the renderer.  It converts touch
## displacement into short, acceleration-limited Cartesian segments and keeps
## enough margin to decelerate before the MOVE_SAFE boundaries.
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import math


PERIOD = 0.05
MAX_QUEUE_AHEAD = 0.20
TOUCH_WATCHDOG = 0.35
DEAD_ZONE = 0.12
EDGE_MARGIN = 0.5
VELOCITY_EPSILON = 0.02


def _clamp(value, minimum, maximum):
    return max(minimum, min(maximum, value))


def radial_input(x, y, center_x, center_y, radius, dead_zone=DEAD_ZONE):
    """Return a unit XY vector; screen-up maps to positive printer Y."""
    dx = (float(x) - center_x) / float(radius)
    dy = (center_y - float(y)) / float(radius)
    magnitude = math.hypot(dx, dy)
    if magnitude <= dead_zone:
        return 0.0, 0.0
    magnitude_clamped = min(1.0, magnitude)
    strength = (magnitude_clamped - dead_zone) / (1.0 - dead_zone)
    return dx / magnitude * strength, dy / magnitude * strength


def vertical_input(y, center_y, radius, dead_zone=DEAD_ZONE):
    """Return Z input with screen-down mapped to positive (bed-down) Z."""
    raw = (float(y) - center_y) / float(radius)
    magnitude = abs(raw)
    if magnitude <= dead_zone:
        return 0.0
    strength = (min(1.0, magnitude) - dead_zone) / (1.0 - dead_zone)
    return math.copysign(strength, raw)


def _approach_vector(current, target, delta):
    difference = tuple(target[index] - current[index]
                       for index in range(len(current)))
    distance = math.sqrt(sum(value * value for value in difference))
    if distance <= delta or distance == 0.0:
        return tuple(target)
    scale = delta / distance
    return tuple(current[index] + difference[index] * scale
                 for index in range(len(current)))


def _boundary_velocity(value, current, position, minimum, maximum,
                       acceleration, dt):
    """Cap the next velocity so this segment plus braking fit in bounds.

    The usual sqrt(2*a*d) cap only describes the speed allowed *at the current
    point*.  With 50 ms segments it may request braking one segment too late.
    Solve the trapezoidal distance of the next segment plus the remaining
    stopping distance instead, leaving the final clamp as a fault guard only.
    """
    direction = 1.0 if value > 0.0 else -1.0 if value < 0.0 else 0.0
    if direction == 0.0:
        return 0.0
    if direction > 0.0:
        distance = max(0.0, maximum - EDGE_MARGIN - position)
    else:
        distance = max(0.0, position - minimum - EDGE_MARGIN)
    toward_edge = max(0.0, current * direction)
    accel_dt = acceleration * dt
    discriminant = (accel_dt * accel_dt
                    - 4.0 * acceleration
                    * (dt * toward_edge - 2.0 * distance))
    maximum_next = max(
        0.0, (-accel_dt + math.sqrt(max(0.0, discriminant))) * 0.5)
    return direction * min(abs(value), maximum_next)
    return 0.0


class Segment:
    def __init__(self, position, speed, acceleration):
        self.position = position
        self.speed = speed
        self.acceleration = acceleration


class JoystickPlanner:
    def __init__(self, xy_speed, xy_accel, z_speed, z_accel, limits):
        self.xy_speed = float(xy_speed)
        self.xy_accel = float(xy_accel)
        self.z_speed = float(z_speed)
        self.z_accel = float(z_accel)
        self.limits = tuple((float(low), float(high)) for low, high in limits)
        self.target = [0.0, 0.0, 0.0]
        self.velocity = [0.0, 0.0, 0.0]
        self.last_touch = 0.0
        self.held = False

    def set_xy(self, x, y, now, center_x, center_y, radius):
        nx, ny = radial_input(x, y, center_x, center_y, radius)
        self.target[:] = [nx * self.xy_speed, ny * self.xy_speed, 0.0]
        self.last_touch = float(now)
        self.held = True

    def set_z(self, y, now, center_y, radius):
        nz = vertical_input(y, center_y, radius)
        self.target[:] = [0.0, 0.0, nz * self.z_speed]
        self.last_touch = float(now)
        self.held = True

    def release(self):
        self.target[:] = [0.0, 0.0, 0.0]
        self.held = False

    def watchdog(self, now):
        if self.held and float(now) - self.last_touch > TOUCH_WATCHDOG:
            self.release()
            return True
        return False

    def is_moving(self):
        return (self.held or any(abs(value) > VELOCITY_EPSILON
                                for value in self.velocity))

    def stop(self):
        self.release()
        self.velocity[:] = [0.0, 0.0, 0.0]

    def advance(self, position, dt=PERIOD):
        dt = max(0.001, min(0.2, float(dt)))
        position = [float(value) for value in position[:3]]
        desired = list(self.target)
        # XY and Z use very different kinematic acceleration limits.  When the
        # operator switches levers before the previous axis group has stopped,
        # finish that deceleration first instead of creating a mixed XYZ move
        # whose single acceleration value could only be correct for one group.
        xy_moving = math.hypot(self.velocity[0], self.velocity[1]) > VELOCITY_EPSILON
        z_moving = abs(self.velocity[2]) > VELOCITY_EPSILON
        if xy_moving and abs(desired[2]) > VELOCITY_EPSILON:
            desired[:] = [0.0, 0.0, 0.0]
        elif z_moving and math.hypot(desired[0], desired[1]) > VELOCITY_EPSILON:
            desired[:] = [0.0, 0.0, 0.0]
        # Reserve enough vector acceleration to brake both planar axes at a
        # corner.  This is intentionally conservative for single-axis motion.
        boundary_xy_accel = self.xy_accel / math.sqrt(2.0)
        for axis in (0, 1):
            desired[axis] = _boundary_velocity(
                desired[axis], self.velocity[axis], position[axis],
                self.limits[axis][0], self.limits[axis][1],
                boundary_xy_accel, dt)
        desired[2] = _boundary_velocity(
            desired[2], self.velocity[2], position[2], self.limits[2][0],
            self.limits[2][1], self.z_accel, dt)

        old_velocity = tuple(self.velocity)
        next_xy = _approach_vector(old_velocity[:2], desired[:2],
                                   self.xy_accel * dt)
        next_z = _approach_vector(old_velocity[2:], desired[2:],
                                  self.z_accel * dt)
        next_velocity = [next_xy[0], next_xy[1], next_z[0]]

        target_position = [
            position[index] + (old_velocity[index] + next_velocity[index])
            * 0.5 * dt for index in range(3)
        ]
        clamped = False
        for axis, (minimum, maximum) in enumerate(self.limits):
            safe_minimum = minimum + EDGE_MARGIN
            safe_maximum = maximum - EDGE_MARGIN
            value = _clamp(target_position[axis], safe_minimum, safe_maximum)
            if value != target_position[axis]:
                next_velocity[axis] = 0.0
                clamped = True
            target_position[axis] = value

        self.velocity[:] = next_velocity
        distance = math.sqrt(sum((target_position[index] - position[index]) ** 2
                                 for index in range(3)))
        if distance < 0.000001:
            if not self.held:
                self.velocity[:] = [0.0, 0.0, 0.0]
            return None
        speed = max(distance / dt,
                    math.sqrt(sum(value * value for value in old_velocity)),
                    math.sqrt(sum(value * value for value in next_velocity)),
                    0.1)
        uses_z = abs(target_position[2] - position[2]) > 0.000001
        acceleration = self.z_accel if uses_z else self.xy_accel
        if clamped and not self.held:
            self.target[:] = [0.0, 0.0, 0.0]
        return Segment(target_position, speed, acceleration)
