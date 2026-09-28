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
    "pid_bed": ("Bed PID", "non_interruptible"),
    "pid_extruder": ("Hotend PID", "non_interruptible"),
    "input_shaper": ("Input Shaper", "non_interruptible"),
}

# Each entry describes the real visible states published by one registered
# operation type and the existing Feather surface responsible for showing it.
# A nested path is intentional: these operations normally run inside the
# parent workflow shown here, and the full breadcrumb is part of the UI.
VISUAL_CONTEXTS = (
    {
        "type": "print", "path": ("print",), "surface": "printing",
        "states": (
            None, "CHECKING FILE", "HOMING", "LEVELING",
            "SKIPPING LEVELING", "LOADING PROFILE",
            "USING LOADED PROFILE", "PARKING", "HEATING BED",
            "HEATING NOZZLE", "RESUMING HEAT", "PRIMING", "PRINTING",
        ),
    },
    {
        "type": "auto_bed_level", "path": ("auto_bed_level",),
        "surface": "calibration", "kind": "mesh",
        "states": (None, "FINISHING"),
    },
    {
        "type": "bed_screws", "path": ("bed_screws",),
        "surface": "calibration", "kind": "screws",
        "states": (
            None, "HOMING", "HEATING", "HEATING NOZZLE",
            "COOLING NOZZLE", "PROBING",
        ),
    },
    {
        "type": "bed_level", "path": ("auto_bed_level", "bed_level"),
        "surface": "calibration", "kind": "mesh",
        "states": (
            None, "HOMING", "HEATING", "HEATING BED", "COOLING BED",
            "HEATING NOZZLE", "COOLING NOZZLE", "LEVELING", "FINISHING",
        ),
    },
    {
        "type": "kamp", "path": ("print", "kamp"),
        "surface": "printing",
        "states": (
            None, "HOMING", "HEATING", "HEATING BED", "COOLING BED",
            "HEATING NOZZLE", "COOLING NOZZLE", "LEVELING",
        ),
    },
    {
        "type": "mesh_validation",
        "path": ("print", "mesh_validation"), "surface": "printing",
        "states": (None, "HOMING", "CHECKING MESH"),
    },
    {
        "type": "nozzle_clean",
        "path": ("auto_bed_level", "bed_level", "nozzle_clean"),
        "surface": "calibration", "kind": "mesh",
        "states": (
            None, "HOMING", "HEATING", "HEATING BED", "COOLING BED",
            "HEATING NOZZLE", "COOLING NOZZLE", "PREPARING TO CLEAN",
            "CLEANING", "FINISHING",
        ),
    },
    {
        "type": "filament", "path": ("filament",),
        "surface": "filament",
        "states": (
            None, "SELECTING MATERIAL", "HEATING NOZZLE",
            "COOLING NOZZLE", "SELECT ACTION", "EXECUTING ACTION",
        ),
    },
    {
        "type": "cold_pull", "path": ("cold_pull",),
        "surface": "cold_pull",
        "states": (
            None, "HOMING", "HEATING", "HEATING NOZZLE", "EXTRUDING",
            "COOLING NOZZLE", "PULLING",
        ),
    },
    {
        "type": "resume", "path": ("print", "resume"),
        "surface": "paused",
        "states": (None, "HEATING NOZZLE", "COOLING NOZZLE"),
    },
    {
        "type": "z_offset", "path": ("z_offset",),
        "surface": "calibration", "kind": "z",
        "states": (
            None, "HOMING", "HEATING", "HEATING NOZZLE",
            "COOLING NOZZLE", "TARING",
        ),
    },
    {
        "type": "recovery", "path": ("recovery",),
        "surface": "calibration", "kind": "recovery",
        "states": (
            None, "LOADING STATE", "PREPARING", "HEATING BED",
            "COOLING BED", "HEATING NOZZLE", "COOLING NOZZLE", "HOMING",
            "POSITIONING", "RESTORING STATE", "FINISHING",
        ),
    },
    {
        "type": "pid_bed", "path": ("pid_bed",),
        "surface": "calibration", "kind": "pid_bed",
        "states": (None, "HOMING", "TUNING", "COMPLETE"),
    },
    {
        "type": "pid_extruder", "path": ("pid_extruder",),
        "surface": "calibration", "kind": "pid_extruder",
        "states": (None, "HOMING", "TUNING", "COMPLETE"),
    },
    {
        "type": "input_shaper", "path": ("input_shaper",),
        "surface": "calibration", "kind": "shaper",
        "states": (
            None, "PREPARING", "HOMING", "MEASURING", "PROCESSING",
            "COMPLETE",
        ),
    },
)


def visual_context_cases():
    for specification in VISUAL_CONTEXTS:
        for state in specification["states"]:
            state_slug = ("starting" if state is None else
                          str(state).lower().replace(
                              "_", "-").replace(" ", "-"))
            label = "ui-context-%s-%s" % (
                specification["type"].replace("_", "-"), state_slug)
            yield specification, state, label

WAIT_VARIANTS = ("HEATING", "COOLING", "NONE")
_MISSING = object()


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


def _iter_snapshots(events, wait_variants=(), optional_variants=None):
    stack = []
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
            yield _snapshot(stack)
        elif kind == "state":
            stack[-1]["current_state"] = event[1]
            yield _snapshot(stack)
        elif kind == "optional_state":
            enabled = True if default_optionals else next(optionals)
            if enabled:
                stack[-1]["current_state"] = event[1]
                yield _snapshot(stack)
        elif kind == "wait":
            variant = next(variants)
            if variant not in WAIT_VARIANTS:
                raise ValueError("unknown temperature fixture variant: %s" % (
                    variant,))
            if variant != "NONE":
                previous = stack[-1]["current_state"]
                stack[-1]["current_state"] = "%s %s" % (
                    variant, event[1])
                yield _snapshot(stack)
                stack[-1]["current_state"] = previous
                yield _snapshot(stack)
        elif kind == "end":
            stack.pop()
            yield _snapshot(stack)
        elif kind == "reset":
            stack[:] = []
            yield _snapshot(stack)
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


def expand_events(events, wait_variants=(), optional_variants=None):
    """Expand compact semantic events into the exact status trace."""
    return list(_iter_snapshots(events, wait_variants, optional_variants))


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


def _variant_name(variants, optionals):
    names = ["HOMING" if enabled else "SKIP_HOMING"
             for enabled in optionals]
    names.extend(variants)
    return ",".join(names) or "default"


def _variant_choices(fixture_name):
    events = FIXTURES[fixture_name]
    wait_count = _wait_count(events)
    optional_count = _optional_count(events)
    wait_choices = (itertools.product(
        WAIT_VARIANTS, repeat=wait_count) if wait_count else ((),))
    optional_choices = tuple(itertools.product(
        (True, False), repeat=optional_count)) if optional_count else ((),)
    for variants in wait_choices:
        for optionals in optional_choices:
            yield _variant_name(variants, optionals), variants, optionals


def exact_variants(fixture_name):
    events = FIXTURES[fixture_name]
    for name, variants, optionals in _variant_choices(fixture_name):
        yield (name, expand_events(
            events, variants, optional_variants=optionals), variants)


def _trace_length(events, variants, optionals):
    return (sum(event[0] in ("begin", "state", "end", "reset")
                for event in events)
            + sum(optionals)
            + 2 * sum(variant != "NONE" for variant in variants))


def _observed_choices(events, actual):
    """Guess choices from visible states; exact expansion still validates them."""
    offset = 0
    variants = []
    optionals = []
    for event in events:
        kind = event[0]
        state = (actual[offset].get("current_state")
                 if offset < len(actual) else None)
        if kind == "optional_state":
            enabled = state == event[1]
            optionals.append(enabled)
            offset += int(enabled)
        elif kind == "wait":
            if state == "HEATING %s" % event[1]:
                variant = "HEATING"
            elif state == "COOLING %s" % event[1]:
                variant = "COOLING"
            else:
                variant = "NONE"
            variants.append(variant)
            offset += 2 if variant != "NONE" else 0
        else:
            offset += 1
    return tuple(variants), tuple(optionals)


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
        for fixture_name in scenario["fixtures"]:
            events = FIXTURES[fixture_name]
            choices, optionals = _observed_choices(events, actual)
            expected = expand_events(events, choices, optionals)
            if expected == actual:
                status = normalize_status(self.manager.get_status(0.0))
                if not status["contexts"]:
                    result = {
                        "scenario": scenario["name"], "passed": True,
                        "fixture": fixture_name,
                        "variant": _variant_name(choices, optionals),
                        "temperature_variants": list(choices),
                        "expected": expected, "actual": actual,
                    }
                    self.results.append(result)
                    return result

        selected = None
        for fixture_name in scenario["fixtures"]:
            events = FIXTURES[fixture_name]
            for variant, choices, optionals in _variant_choices(fixture_name):
                prefix = 0
                matched = True
                for expected_snapshot, observed in itertools.zip_longest(
                        _iter_snapshots(events, choices, optionals), actual,
                        fillvalue=_MISSING):
                    if expected_snapshot != observed:
                        matched = False
                        break
                    prefix += 1
                score = (prefix, -abs(
                    _trace_length(events, choices, optionals) - len(actual)))
                if selected is None or score > selected[4]:
                    selected = (fixture_name, variant, choices, optionals, score)
                if matched:
                    status = normalize_status(self.manager.get_status(0.0))
                    if status["contexts"]:
                        break
                    expected = expand_events(events, choices, optionals)
                    result = {
                        "scenario": scenario["name"], "passed": True,
                        "fixture": fixture_name, "variant": variant,
                        "temperature_variants": list(choices),
                        "expected": expected, "actual": actual,
                    }
                    self.results.append(result)
                    return result
        expected = (expand_events(FIXTURES[selected[0]], selected[2], selected[3])
                    if selected is not None else [])
        diagnostic = first_difference(expected, actual)
        final_status = normalize_status(self.manager.get_status(0.0))
        if final_status["contexts"]:
            diagnostic = "%s; final operation stack is not empty" % (
                diagnostic or "trace differs",)
        result = {
            "scenario": scenario["name"], "passed": False,
            "fixture": selected[0] if selected is not None else None,
            "variant": selected[1] if selected is not None else None,
            "temperature_variants": (
                list(selected[2]) if selected is not None else []),
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
