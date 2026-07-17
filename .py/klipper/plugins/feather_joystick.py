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


# Motion runs from a fixed clock rather than the raw touch event rate. Keeping
# only a few short segments queued makes release and direction changes visible
# at the toolhead without starving Klipper's lookahead.
PERIOD = 0.010
QUEUE_RETRY = 0.005
FEEDBACK_PERIOD = 0.05
TOUCH_WATCHDOG = 0.15
MAX_SPEED_SCALE = 0.5
DEAD_ZONE = 0.12
EDGE_MARGIN = 0.5
VELOCITY_EPSILON = 0.02
ACCELERATION_EPSILON = 0.5
FORCE_TIME_CONSTANT = 0.20
STOP_FORCE_MULTIPLIER = 1.8
JERK_RISE_TIME = 0.05


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


def _magnitude(values):
    return math.sqrt(sum(value * value for value in values))


def _limit_vector(values, limit):
    magnitude = _magnitude(values)
    if magnitude <= limit or magnitude == 0.0:
        return tuple(values)
    scale = limit / magnitude
    return tuple(value * scale for value in values)


def _straight_braking(velocity, acceleration, limit, jerk, dt):
    """Brake without allowing retained lateral acceleration to turn motion."""
    speed = _magnitude(velocity)
    if speed <= VELOCITY_EPSILON:
        zero = tuple(0.0 for _value in velocity)
        return zero, zero
    direction = tuple(value / speed for value in velocity)
    parallel_acceleration = sum(
        acceleration[index] * direction[index]
        for index in range(len(velocity)))
    desired_acceleration = -min(limit, speed / dt)
    acceleration_delta = _clamp(
        desired_acceleration - parallel_acceleration, -jerk * dt, jerk * dt)
    next_parallel_acceleration = parallel_acceleration + acceleration_delta
    next_speed = speed + (
        parallel_acceleration + next_parallel_acceleration) * 0.5 * dt
    if next_speed <= VELOCITY_EPSILON:
        zero = tuple(0.0 for _value in velocity)
        return zero, zero
    return (
        tuple(value * next_speed for value in direction),
        tuple(value * next_parallel_acceleration for value in direction),
    )


def _boundary_velocity(value, current, position, minimum, maximum,
                       acceleration, dt, minimum_margin=EDGE_MARGIN,
                       maximum_margin=EDGE_MARGIN):
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
        distance = max(0.0, maximum - maximum_margin - position)
    else:
        distance = max(0.0, position - minimum - minimum_margin)
    toward_edge = max(0.0, current * direction)
    accel_dt = acceleration * dt
    discriminant = (accel_dt * accel_dt
                    - 4.0 * acceleration
                    * (dt * toward_edge - 2.0 * distance))
    maximum_next = max(
        0.0, (-accel_dt + math.sqrt(max(0.0, discriminant))) * 0.5)
    return direction * min(abs(value), maximum_next)


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
        # Full deflection applies a force. Linear drag makes the terminal
        # velocity equal to the configured speed without turning the stick
        # position into an instantaneous velocity command.
        self.xy_force = min(
            self.xy_accel, self.xy_speed / FORCE_TIME_CONSTANT)
        self.z_force = min(
            self.z_accel, self.z_speed / FORCE_TIME_CONSTANT)
        self.xy_drag = self.xy_force / max(self.xy_speed, 0.1)
        self.z_drag = self.z_force / max(self.z_speed, 0.1)
        self.xy_brake = min(
            self.xy_accel, self.xy_force * STOP_FORCE_MULTIPLIER)
        self.z_brake = min(
            self.z_accel, self.z_force * STOP_FORCE_MULTIPLIER)
        self.xy_jerk = self.xy_force / JERK_RISE_TIME
        self.z_jerk = self.z_force / JERK_RISE_TIME
        self.target = [0.0, 0.0, 0.0]
        self.velocity = [0.0, 0.0, 0.0]
        self.acceleration = [0.0, 0.0, 0.0]
        self.last_touch = 0.0
        self.held = False

    def set_xy(self, x, y, now, center_x, center_y, radius):
        nx, ny = radial_input(x, y, center_x, center_y, radius)
        self.target[:] = [nx, ny, 0.0]
        self.last_touch = float(now)
        self.held = True

    def set_z(self, y, now, center_y, radius):
        nz = vertical_input(y, center_y, radius)
        self.target[:] = [0.0, 0.0, nz]
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
        self.acceleration[:] = [0.0, 0.0, 0.0]

    def inertia(self):
        return {
            "velocity": tuple(self.velocity),
            "acceleration": tuple(self.acceleration),
            "xy_speed": math.hypot(self.velocity[0], self.velocity[1]),
            "z_speed": self.velocity[2],
            "acceleration_magnitude": _magnitude(self.acceleration),
        }

    def _advance_group(self, indices, control, speed_limit, force_limit,
                       drag, brake_limit, jerk_limit, dt):
        old_velocity = tuple(self.velocity[index] for index in indices)
        old_acceleration = tuple(
            self.acceleration[index] for index in indices)
        if _magnitude(control) > VELOCITY_EPSILON:
            desired_acceleration = tuple(
                force_limit * control[offset] - drag * old_velocity[offset]
                for offset in range(len(indices)))
            desired_acceleration = _limit_vector(
                desired_acceleration, brake_limit)
            next_acceleration = _approach_vector(
                old_acceleration, desired_acceleration, jerk_limit * dt)
            next_velocity = tuple(
                old_velocity[offset]
                + (old_acceleration[offset] + next_acceleration[offset])
                * 0.5 * dt for offset in range(len(indices)))
            next_velocity = list(_limit_vector(next_velocity, speed_limit))
            next_acceleration = list(next_acceleration)

            # A component with no stick force may coast and decay, but it must
            # never cross zero and accelerate in the opposite direction. That
            # retained transverse acceleration was the source of circular
            # "fly-off" paths after sharp XY direction changes.
            for offset, value in enumerate(control):
                old_value = old_velocity[offset]
                next_value = next_velocity[offset]
                settles = (
                    abs(value) <= VELOCITY_EPSILON
                    and old_value * next_value <= 0.0
                )
                opposes_held_input = (
                    value > VELOCITY_EPSILON
                    and old_value >= 0.0 and next_value < 0.0
                ) or (
                    value < -VELOCITY_EPSILON
                    and old_value <= 0.0 and next_value > 0.0
                )
                if settles or opposes_held_input:
                    next_velocity[offset] = 0.0
                    next_acceleration[offset] = 0.0
        else:
            next_velocity, next_acceleration = _straight_braking(
                old_velocity, old_acceleration, brake_limit, jerk_limit, dt)

        for offset, index in enumerate(indices):
            self.velocity[index] = next_velocity[offset]
            self.acceleration[index] = next_acceleration[offset]

    def advance(self, position, dt=PERIOD):
        dt = max(0.001, min(0.2, float(dt)))
        position = [float(value) for value in position[:3]]
        control = list(self.target)
        # XY and Z use very different kinematic acceleration limits.  When the
        # operator switches levers before the previous axis group has stopped,
        # finish that deceleration first instead of creating a mixed XYZ move
        # whose single acceleration value could only be correct for one group.
        xy_moving = math.hypot(self.velocity[0], self.velocity[1]) > VELOCITY_EPSILON
        z_moving = abs(self.velocity[2]) > VELOCITY_EPSILON
        if xy_moving and abs(control[2]) > VELOCITY_EPSILON:
            control[:] = [0.0, 0.0, 0.0]
        elif z_moving and math.hypot(control[0], control[1]) > VELOCITY_EPSILON:
            control[:] = [0.0, 0.0, 0.0]

        old_velocity = tuple(self.velocity)
        self._advance_group(
            (0, 1), control[:2], self.xy_speed, self.xy_force,
            self.xy_drag, self.xy_brake, self.xy_jerk, dt)
        self._advance_group(
            (2,), control[2:], self.z_speed, self.z_force,
            self.z_drag, self.z_brake, self.z_jerk, dt)
        next_velocity = list(self.velocity)

        # Reserve enough vector braking force for both planar axes at a corner.
        boundary_xy_accel = self.xy_brake / math.sqrt(2.0)
        for axis in (0, 1):
            next_velocity[axis] = _boundary_velocity(
                next_velocity[axis], old_velocity[axis], position[axis],
                self.limits[axis][0], self.limits[axis][1],
                boundary_xy_accel, dt)
        next_velocity[2] = _boundary_velocity(
            next_velocity[2], old_velocity[2], position[2],
            self.limits[2][0], self.limits[2][1], self.z_brake, dt,
            minimum_margin=0.0)

        boundary_settled = [False, False, False]
        for axis in range(3):
            # Boundary braking may carry negative acceleration into the next
            # integration step.  Never let it reverse the axis against a stick
            # that is still held outward; reaching the wall is a settled stop,
            # not an elastic collision.
            if (control[axis] > VELOCITY_EPSILON
                    and old_velocity[axis] >= 0.0
                    and next_velocity[axis] < 0.0):
                next_velocity[axis] = 0.0
                boundary_settled[axis] = True
            elif (control[axis] < -VELOCITY_EPSILON
                    and old_velocity[axis] <= 0.0
                    and next_velocity[axis] > 0.0):
                next_velocity[axis] = 0.0
                boundary_settled[axis] = True

        target_position = [
            position[index] + (old_velocity[index] + next_velocity[index])
            * 0.5 * dt for index in range(3)
        ]
        clamped = False
        clamped_axes = [False, False, False]
        for axis, (minimum, maximum) in enumerate(self.limits):
            axis_active = (
                abs(control[axis]) > VELOCITY_EPSILON
                or abs(old_velocity[axis]) > VELOCITY_EPSILON
                or abs(next_velocity[axis]) > VELOCITY_EPSILON
            )
            if not axis_active:
                # Homing and probing may leave an axis a fraction beyond the
                # joystick's conservative EDGE_MARGIN.  Never pull unrelated
                # axes into that margin (an XY gesture must not become XYZ).
                target_position[axis] = position[axis]
                continue
            # Z=0 is the physical lower limit and must remain reachable.
            # XY and the upper Z end retain their conservative margin.
            safe_minimum = minimum + (0.0 if axis == 2 else EDGE_MARGIN)
            safe_maximum = maximum - EDGE_MARGIN
            if position[axis] > safe_maximum:
                value = (target_position[axis]
                         if target_position[axis] < position[axis]
                         else position[axis])
            elif position[axis] < safe_minimum:
                value = (target_position[axis]
                         if target_position[axis] > position[axis]
                         else position[axis])
            else:
                value = _clamp(
                    target_position[axis], safe_minimum, safe_maximum)
            if value != target_position[axis]:
                next_velocity[axis] = 0.0
                clamped_axes[axis] = True
                clamped = True
            target_position[axis] = value

        self.velocity[:] = next_velocity
        for axis in range(3):
            if clamped_axes[axis] or boundary_settled[axis]:
                # Treat a final range clamp as a settled stop, not an elastic
                # collision whose stored acceleration could bounce inward.
                self.acceleration[axis] = 0.0
                continue
            actual_acceleration = (
                next_velocity[axis] - old_velocity[axis]) / dt
            if (abs(actual_acceleration - self.acceleration[axis])
                    > ACCELERATION_EPSILON):
                self.acceleration[axis] = actual_acceleration
        distance = math.sqrt(sum((target_position[index] - position[index]) ** 2
                                 for index in range(3)))
        if distance < 0.000001:
            if (not self.held
                    or _magnitude(self.target) <= VELOCITY_EPSILON):
                self.velocity[:] = [0.0, 0.0, 0.0]
                self.acceleration[:] = [0.0, 0.0, 0.0]
            return None
        speed = max(distance / dt,
                    math.sqrt(sum(value * value for value in old_velocity)),
                    math.sqrt(sum(value * value for value in next_velocity)),
                    0.1)
        uses_z = abs(target_position[2] - position[2]) > 0.000001
        # Segment acceleration is a transport allowance for Klipper's
        # lookahead, not the joystick's physical acceleration.  The latter is
        # already encoded in each endpoint and velocity above.  Keeping the
        # allowance at the configured axis limit prevents a slowly ramping
        # gesture from being held in lookahead for hundreds of milliseconds.
        acceleration = self.z_accel if uses_z else self.xy_accel
        if clamped and not self.held:
            self.target[:] = [0.0, 0.0, 0.0]
        return Segment(target_position, speed, acceleration)
