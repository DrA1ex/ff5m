## Operation-context fixtures for the Feather on-printer test runner.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import itertools


CONTEXT_TYPES = {
    "print": ("Print", "cancelable"),
    "auto_bed_level": ("Bed Level", "cancelable"),
    "bed_screws": ("Bed Screws", "cancelable"),
    "bed_level": ("Bed Mesh", "interruptible"),
    "kamp": ("KAMP", "interruptible"),
    "mesh_validation": ("Mesh Validation", "interruptible"),
    "nozzle_clean": ("Nozzle Cleaning", "interruptible"),
    "filament": ("Filament", "cancelable"),
    "cold_pull": ("Cold Pull", "cancelable"),
    "resume": ("Resume", "interruptible"),
    "z_offset": ("Z Offset", "cancelable"),
    "recovery": ("Recovery", "non_interruptible"),
}

WAIT_VARIANTS = ("HEATING", "COOLING", "NONE")


class FixtureMismatch(RuntimeError):
    def __init__(self, scenario, diagnostic, expected=None, actual=None):
        RuntimeError.__init__(
            self, "operation_context fixture mismatch in %s: %s" % (
                scenario, diagnostic))
        self.scenario = scenario
        self.diagnostic = diagnostic
        self.expected = expected
        self.actual = actual


def normalize_status(status):
    """Remove IDs, revisions and other non-semantic status fields."""
    contexts = []
    for frame in status.get("contexts", ()):
        contexts.append({
            "type": str(frame.get("type", "")),
            "name": str(frame.get("name", "")),
            "current_state": frame.get("current_state"),
            "cancel_mode": str(frame.get("cancel_mode", "")),
        })
    return {
        "contexts": contexts,
        "context_path": [str(value) for value in status.get(
            "context_path", ())],
        "current_state": status.get("current_state"),
        "cancel_available": bool(status.get("cancel_available", False)),
        "cancel_pending": bool(status.get("cancel_pending", False)),
        "cancel_target": {
            "type": status.get("cancel_target_type"),
            "name": status.get("cancel_target_name"),
            "mode": status.get("cancel_target_mode"),
        },
        "cancel_blocker": {
            "type": status.get("cancel_blocker_type"),
            "name": status.get("cancel_blocker_name"),
        },
    }


def temperature_variant(temperature, minimum, maximum):
    """Use the same explicit boundaries as _WAIT_TEMPERATURE."""
    temperature = float(temperature)
    minimum = float(minimum)
    maximum = float(maximum)
    if temperature < minimum:
        return "HEATING"
    if temperature > maximum:
        return "COOLING"
    return "NONE"


def _cancel_decision(stack):
    for frame in reversed(stack):
        mode = frame["cancel_mode"]
        if mode == "cancelable":
            return frame, None
        if mode == "non_interruptible":
            return None, frame
    return (stack[0], None) if stack else (None, None)


def _snapshot(stack):
    target, blocker = _cancel_decision(stack)
    contexts = [dict(frame) for frame in stack]
    return {
        "contexts": contexts,
        "context_path": [frame["name"] for frame in stack],
        "current_state": (stack[-1]["current_state"] if stack else None),
        "cancel_available": target is not None,
        "cancel_pending": False,
        "cancel_target": {
            "type": target["type"] if target else None,
            "name": target["name"] if target else None,
            "mode": target["cancel_mode"] if target else None,
        },
        "cancel_blocker": {
            "type": blocker["type"] if blocker else None,
            "name": blocker["name"] if blocker else None,
        },
    }


def expand_events(events, wait_variants=(), optional_variants=None):
    """Expand compact semantic events into the exact status trace."""
    stack = []
    snapshots = []
    variants = iter(wait_variants)
    default_optionals = optional_variants is None
    optionals = iter(optional_variants or ())
    for event in events:
        kind = event[0]
        if kind == "begin":
            type_id = event[1]
            name, cancel_mode = CONTEXT_TYPES[type_id]
            stack.append({
                "type": type_id, "name": name,
                "current_state": None, "cancel_mode": cancel_mode,
            })
            snapshots.append(_snapshot(stack))
        elif kind == "state":
            stack[-1]["current_state"] = event[1]
            snapshots.append(_snapshot(stack))
        elif kind == "optional_state":
            enabled = True if default_optionals else next(optionals)
            if enabled:
                stack[-1]["current_state"] = event[1]
                snapshots.append(_snapshot(stack))
        elif kind == "wait":
            variant = next(variants)
            if variant not in WAIT_VARIANTS:
                raise ValueError("unknown temperature fixture variant: %s" % (
                    variant,))
            if variant != "NONE":
                previous = stack[-1]["current_state"]
                stack[-1]["current_state"] = "%s %s" % (
                    variant, event[1])
                snapshots.append(_snapshot(stack))
                stack[-1]["current_state"] = previous
                snapshots.append(_snapshot(stack))
        elif kind == "end":
            stack.pop()
            snapshots.append(_snapshot(stack))
        elif kind == "reset":
            stack[:] = []
            snapshots.append(_snapshot(stack))
        else:
            raise ValueError("unknown fixture event: %s" % (kind,))
    try:
        next(variants)
    except StopIteration:
        pass
    else:
        raise ValueError("too many temperature fixture variants")
    if not default_optionals:
        try:
            next(optionals)
        except StopIteration:
            pass
        else:
            raise ValueError("too many optional fixture variants")
    return snapshots


NO_CONTEXT = ()
SCREWS = (
    ("begin", "bed_screws"), ("optional_state", "HOMING"),
    ("state", "HEATING"), ("wait", "NOZZLE"),
    ("state", "PROBING"), ("end",),
)
Z_OFFSET_SKIP_CLEAN = (
    ("begin", "z_offset"), ("optional_state", "HOMING"),
    ("state", "HEATING"), ("wait", "NOZZLE"),
    ("state", "TARING"), ("end",),
)
NOZZLE_CLEAN = (
    ("begin", "nozzle_clean"), ("optional_state", "HOMING"),
    ("state", "HEATING"), ("wait", "BED"), ("wait", "NOZZLE"),
    ("state", "PREPARING TO CLEAN"), ("state", "CLEANING"),
    ("wait", "NOZZLE"), ("state", "FINISHING"), ("end",),
)
MESH_CLEAN = (
    ("begin", "auto_bed_level"), ("begin", "bed_level"),
) + NOZZLE_CLEAN + (
    ("state", "LEVELING"), ("state", "FINISHING"), ("end",),
    ("state", "FINISHING"), ("end",),
)
MESH_SKIP_CLEAN = (
    ("begin", "auto_bed_level"), ("begin", "bed_level"),
    ("optional_state", "HOMING"), ("state", "HEATING"),
    ("wait", "BED"), ("wait", "NOZZLE"),
    ("state", "LEVELING"), ("state", "FINISHING"), ("end",),
    ("state", "FINISHING"), ("end",),
)
FILAMENT = (
    ("begin", "filament"), ("state", "SELECTING MATERIAL"),
    ("wait", "NOZZLE"), ("state", "SELECT ACTION"),
    ("state", "EXECUTING ACTION"), ("state", "SELECT ACTION"),
    ("state", "EXECUTING ACTION"), ("state", "SELECT ACTION"),
    ("state", "EXECUTING ACTION"), ("state", "SELECT ACTION"),
    ("end",),
)
COLD_PULL = (
    ("begin", "cold_pull"), ("optional_state", "HOMING"),
    ("state", "HEATING"), ("wait", "NOZZLE"),
    ("state", "EXTRUDING"),
    ("wait", "NOZZLE"), ("state", "PULLING"), ("end",),
)
PRINT_KAMP = (
    ("begin", "print"), ("optional_state", "HOMING"),
    ("state", "LEVELING"), ("begin", "kamp"),
) + NOZZLE_CLEAN + (
    ("state", "LEVELING"), ("end",), ("state", "PARKING"),
    ("wait", "BED"), ("wait", "NOZZLE"),
    ("state", "PRIMING"), ("state", "PRINTING"), ("reset",),
)
PRINT_MESH_RESUME = (
    ("begin", "print"), ("optional_state", "HOMING"),
    ("state", "LEVELING"), ("state", "USING LOADED PROFILE"),
    ("state", "PARKING"), ("wait", "BED"),
    ("wait", "NOZZLE"), ("begin", "mesh_validation"),
    ("optional_state", "HOMING"),
    ("state", "CHECKING MESH"), ("end",),
    ("state", "RESUMING HEAT"), ("wait", "NOZZLE"),
    ("state", "PRIMING"), ("state", "PRINTING"),
    ("reset",),
)
RECOVERY = (
    ("begin", "recovery"), ("state", "LOADING STATE"),
    ("state", "PREPARING"), ("wait", "BED"),
    ("wait", "NOZZLE"), ("optional_state", "HOMING"),
    ("state", "POSITIONING"), ("state", "RESTORING STATE"),
    ("end",), ("begin", "print"), ("state", "PRINTING"),
    ("reset",),
)


FIXTURES = {
    "none": NO_CONTEXT,
    "screws": SCREWS,
    "mesh_clean": MESH_CLEAN,
    "mesh_skip_clean": MESH_SKIP_CLEAN,
    "z_offset_skip_clean": Z_OFFSET_SKIP_CLEAN,
    "filament": FILAMENT,
    "cold_pull": COLD_PULL,
    "print_kamp": PRINT_KAMP,
    "print_mesh_resume": PRINT_MESH_RESUME,
    "recovery": RECOVERY,
}


def _wait_count(events):
    return sum(1 for event in events if event[0] == "wait")


def _optional_count(events):
    return sum(1 for event in events if event[0] == "optional_state")


def exact_variants(fixture_name):
    events = FIXTURES[fixture_name]
    wait_count = _wait_count(events)
    optional_count = _optional_count(events)
    wait_choices = (itertools.product(
        WAIT_VARIANTS, repeat=wait_count) if wait_count else ((),))
    optional_choices = tuple(itertools.product(
        (True, False), repeat=optional_count)) if optional_count else ((),)
    for variants in wait_choices:
        for optionals in optional_choices:
            names = ["HOMING" if enabled else "SKIP_HOMING"
                     for enabled in optionals]
            names.extend(variants)
            name = ",".join(names) or "default"
            yield (name, expand_events(
                events, variants, optional_variants=optionals), variants)


def first_difference(expected, actual):
    length = min(len(expected), len(actual))
    for index in range(length):
        if expected[index] != actual[index]:
            return ("snapshot %d differs: expected=%r actual=%r" % (
                index, expected[index], actual[index]))
    if len(expected) != len(actual):
        return "trace length differs: expected=%d actual=%d" % (
            len(expected), len(actual))
    return None


def _common_prefix(expected, actual):
    count = 0
    for left, right in zip(expected, actual):
        if left != right:
            break
        count += 1
    return count


class OperationContextRecorder:
    """Reversibly records semantic operation_context transitions."""

    def __init__(self, manager):
        self.manager = manager
        self.attached = False
        self._had_instance_changed = False
        self._instance_changed = None
        self._original_changed = None
        self._trace = []
        self._scenario = None
        self.results = []

    def attach(self):
        if self.attached:
            return
        namespace = getattr(self.manager, "__dict__", {})
        self._had_instance_changed = "_changed" in namespace
        self._instance_changed = namespace.get("_changed")
        self._original_changed = self.manager._changed

        def changed(*args, **kwargs):
            try:
                return self._original_changed(*args, **kwargs)
            finally:
                try:
                    self._trace.append(normalize_status(
                        self.manager.get_status(0.0)))
                except Exception:
                    # Test instrumentation must never alter product behavior.
                    pass

        self.manager._changed = changed
        self.attached = True

    def detach(self):
        if not self.attached:
            return
        if self._had_instance_changed:
            self.manager._changed = self._instance_changed
        else:
            try:
                del self.manager.__dict__["_changed"]
            except (AttributeError, KeyError):
                pass
        self.attached = False

    def start_scenario(self, name, fixtures):
        if self._scenario is not None:
            raise RuntimeError("operation_context scenario already active")
        status = normalize_status(self.manager.get_status(0.0))
        unexpected = list(self._trace)
        if status["contexts"] or unexpected:
            diagnostic = (
                "operation stack is not empty at start"
                if status["contexts"] else
                "operation transitions occurred between scenarios")
            actual = unexpected + ([status] if status["contexts"] else [])
            self.results.append({
                "scenario": str(name), "passed": False,
                "fixture": None, "variant": None,
                "diagnostic": diagnostic, "expected": [],
                "actual": actual,
            })
            self._trace = []
            raise FixtureMismatch(name, diagnostic, [], actual)
        self._trace = []
        self._scenario = {
            "name": str(name),
            "fixtures": tuple(fixtures),
        }

    def finish_scenario(self):
        if self._scenario is None:
            raise RuntimeError("no operation_context scenario is active")
        scenario = self._scenario
        self._scenario = None
        actual = list(self._trace)
        self._trace = []
        candidates = []
        for fixture_name in scenario["fixtures"]:
            for variant, expected, choices in exact_variants(fixture_name):
                candidates.append((fixture_name, variant, expected, choices))
                if expected == actual:
                    status = normalize_status(self.manager.get_status(0.0))
                    if status["contexts"]:
                        break
                    result = {
                        "scenario": scenario["name"], "passed": True,
                        "fixture": fixture_name, "variant": variant,
                        "temperature_variants": list(choices),
                        "expected": expected, "actual": actual,
                    }
                    self.results.append(result)
                    return result
        selected = (max(
            candidates,
            key=lambda item: (
                _common_prefix(item[2], actual),
                -abs(len(item[2]) - len(actual))))
            if candidates else (None, None, [], ()))
        expected = selected[2]
        diagnostic = first_difference(expected, actual)
        final_status = normalize_status(self.manager.get_status(0.0))
        if final_status["contexts"]:
            diagnostic = "%s; final operation stack is not empty" % (
                diagnostic or "trace differs",)
        result = {
            "scenario": scenario["name"], "passed": False,
            "fixture": selected[0],
            "variant": selected[1],
            "temperature_variants": (
                list(selected[3]) if candidates else []),
            "diagnostic": diagnostic, "expected": expected,
            "actual": actual,
        }
        self.results.append(result)
        raise FixtureMismatch(
            scenario["name"], diagnostic, expected=expected, actual=actual)

    def abort_active(self, reason):
        if self._scenario is None:
            return
        scenario = self._scenario
        self._scenario = None
        self.results.append({
            "scenario": scenario["name"], "passed": False,
            "fixture": None, "variant": None,
            "diagnostic": str(reason), "expected": None,
            "actual": list(self._trace),
        })
        self._trace = []

    def report(self):
        return {
            "passed": bool(self.results) and all(
                item.get("passed", False) for item in self.results),
            "scenarios": list(self.results),
        }
