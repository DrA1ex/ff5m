## Operation context tracking and cooperative cancellation for G-code workflows.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import logging
import re


TYPE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
CANCEL_MODE_INTERRUPTIBLE = "interruptible"
CANCEL_MODE_CANCELABLE = "cancelable"
CANCEL_MODE_NON_INTERRUPTIBLE = "non_interruptible"
CANCEL_MODES = frozenset((
    CANCEL_MODE_INTERRUPTIBLE,
    CANCEL_MODE_CANCELABLE,
    CANCEL_MODE_NON_INTERRUPTIBLE,
))


class ContextType:
    def __init__(self, type_id, name,
                 cancel_mode=CANCEL_MODE_INTERRUPTIBLE, on_cancel=None):
        self.type_id = type_id
        self.name = name
        self.cancel_mode = cancel_mode
        self.on_cancel = on_cancel


class ContextFrame:
    def __init__(self, frame_id, definition):
        self.frame_id = frame_id
        self.definition = definition
        self.current_state = None
        self.saved_states = []


class CancellationRequest:
    def __init__(self, request_id, target_id):
        self.request_id = request_id
        self.target_id = target_id


class OperationContextManager:
    cmd_CONTEXT_BEGIN_help = "Begin a registered operation context"
    cmd_CONTEXT_STATE_help = "Set the active operation state"
    cmd_CONTEXT_END_help = "End the active operation context"
    cmd_CONTEXT_CANCEL_help = "Request cancellation of the nearest domain"
    cmd_CONTEXT_CANCEL_POINT_help = "Deliver a pending operation cancellation"
    cmd_CONTEXT_RESET_help = "Reset all operation contexts"

    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object("gcode")
        self.context_types = {}
        self.contexts = []
        self.pending_cancel = None
        self.cancelling = False
        self.revision = 0
        self.next_frame_id = 1
        self.next_cancel_request_id = 1

        commands = (
            ("_CONTEXT_BEGIN", self.cmd_CONTEXT_BEGIN,
             self.cmd_CONTEXT_BEGIN_help),
            ("_CONTEXT_STATE", self.cmd_CONTEXT_STATE,
             self.cmd_CONTEXT_STATE_help),
            ("_CONTEXT_END", self.cmd_CONTEXT_END,
             self.cmd_CONTEXT_END_help),
            ("_CONTEXT_CANCEL", self.cmd_CONTEXT_CANCEL,
             self.cmd_CONTEXT_CANCEL_help),
            ("_CONTEXT_CANCEL_POINT", self.cmd_CONTEXT_CANCEL_POINT,
             self.cmd_CONTEXT_CANCEL_POINT_help),
            ("_CONTEXT_RESET", self.cmd_CONTEXT_RESET,
             self.cmd_CONTEXT_RESET_help),
        )
        for name, handler, description in commands:
            self.gcode.register_command(name, handler, desc=description)
        register_immediate = getattr(
            self.gcode, "register_immediate_command", None)
        if register_immediate is not None:
            register_immediate("_CONTEXT_CANCEL")

        for event in (
                "gcode:command_error", "gcode:request_restart",
                "klippy:shutdown", "klippy:disconnect"):
            self.printer.register_event_handler(
                event, self._handle_interrupted_operation)

    def register_context_type(self, config):
        section = config.get_name().split(None, 1)
        if len(section) != 2:
            raise config.error(
                "operation_context_type requires a type identifier")
        type_id = self._normalize_type(section[1])
        if type_id is None:
            raise config.error(
                "Invalid operation context type '%s'" % (section[1],))
        name = str(config.get("name", type_id.replace("_", " "))).strip()
        if not name:
            raise config.error(
                "operation_context_type %s requires a non-empty name"
                % (type_id,))
        cancel_mode = str(config.get(
            "cancel_mode", CANCEL_MODE_INTERRUPTIBLE)).strip().lower()
        if cancel_mode not in CANCEL_MODES:
            raise config.error(
                "operation_context_type %s has invalid cancel_mode '%s'; "
                "expected one of: %s" % (
                    type_id, cancel_mode, ", ".join(sorted(CANCEL_MODES))))
        on_cancel = str(config.get("on_cancel", "")).strip() or None
        definition = ContextType(
            type_id, name, cancel_mode=cancel_mode, on_cancel=on_cancel)
        self.context_types[type_id] = definition
        return definition

    def get_status(self, eventtime):
        del eventtime
        target, blocker = self._cancel_decision()
        pending = self.pending_cancel
        if pending is not None:
            pending_target = self._frame_by_id(pending.target_id)
            if pending_target is not None:
                target, blocker = pending_target, None
        frames = tuple({
            "id": frame.frame_id,
            "type": frame.definition.type_id,
            "name": frame.definition.name,
            "current_state": frame.current_state,
            "cancel_mode": frame.definition.cancel_mode,
        } for frame in self.contexts)
        return {
            "contexts": frames,
            "context_path": tuple(
                frame.definition.name for frame in self.contexts),
            "context_types": tuple(
                frame.definition.type_id for frame in self.contexts),
            "current_state": (self.contexts[-1].current_state
                              if self.contexts else None),
            "cancel_available": target is not None,
            "cancel_pending": pending is not None,
            "cancel_request_id": (
                pending.request_id if pending is not None else None),
            "cancel_target_id": (target.frame_id if target else None),
            "cancel_target_type": (
                target.definition.type_id if target else None),
            "cancel_target_name": (
                target.definition.name if target else None),
            "cancel_target_mode": (
                target.definition.cancel_mode if target else None),
            "cancel_blocker_id": (blocker.frame_id if blocker else None),
            "cancel_blocker_type": (
                blocker.definition.type_id if blocker else None),
            "cancel_blocker_name": (
                blocker.definition.name if blocker else None),
            "revision": self.revision,
        }

    def request_cancel(self):
        if self.pending_cancel is not None:
            target = self._frame_by_id(self.pending_cancel.target_id)
            if target is not None:
                return self._cancel_result(
                    "already_pending", target,
                    request=self.pending_cancel)
            self.pending_cancel = None
            self._changed()
        target, blocker = self._cancel_decision()
        if target is None:
            status = ("non_interruptible" if blocker is not None
                      else "not_cancelable")
            return self._cancel_result(status, None, blocker=blocker)
        request = CancellationRequest(
            self.next_cancel_request_id, target.frame_id)
        self.next_cancel_request_id += 1
        self.pending_cancel = request
        self._changed()
        logging.info(
            "[operation_context] cancellation request #%d for %s#%d",
            request.request_id, target.definition.type_id, target.frame_id)
        return self._cancel_result("accepted", target, request=request)

    def clear_cancel(self, request_id=None):
        request = self.pending_cancel
        if request is None:
            return {"status": "not_pending", "cleared": False,
                    "request_id": None}
        if self.cancelling:
            return {"status": "too_late", "cleared": False,
                    "request_id": request.request_id}
        if (request_id is not None
                and int(request_id) != request.request_id):
            return {"status": "stale_request", "cleared": False,
                    "request_id": request.request_id}
        target = self._frame_by_id(request.target_id)
        self.pending_cancel = None
        self._changed()
        logging.info(
            "[operation_context] cancellation request #%d cleared",
            request.request_id)
        return {
            "status": "cleared", "cleared": True,
            "request_id": request.request_id,
            "target_id": target.frame_id if target else None,
            "target_type": (
                target.definition.type_id if target else None),
            "target_name": (
                target.definition.name if target else None),
        }

    def cancellation_point(self, gcmd):
        if self.cancelling or self.pending_cancel is None:
            return False
        request = self.pending_cancel
        target = self._frame_by_id(request.target_id)
        if target is None:
            self.pending_cancel = None
            self._changed()
            self._warn(gcmd, "pending cancellation target no longer exists")
            return False
        self._abort_cancelled_operation(gcmd, target)
        return True

    def _cancel_result(self, status, target, request=None, blocker=None):
        return {
            "status": status,
            "accepted": status in ("accepted", "already_pending"),
            "request_id": request.request_id if request else None,
            "target_id": target.frame_id if target else None,
            "target_type": target.definition.type_id if target else None,
            "target_name": target.definition.name if target else None,
            "target_mode": (
                target.definition.cancel_mode if target else None),
            "blocker_id": blocker.frame_id if blocker else None,
            "blocker_type": (
                blocker.definition.type_id if blocker else None),
            "blocker_name": (
                blocker.definition.name if blocker else None),
        }

    def _normalize_type(self, value):
        type_id = str(value).strip().lower()
        return type_id if TYPE_RE.match(type_id) else None

    def _type(self, gcmd):
        value = gcmd.get("TYPE", None)
        type_id = self._normalize_type("" if value is None else value)
        if type_id is None:
            self._warn(
                gcmd, "_CONTEXT_BEGIN requires a valid non-empty TYPE")
            return None
        definition = self.context_types.get(type_id)
        if definition is None:
            self._warn(gcmd, "unknown context type '%s'" % (type_id,))
        return definition

    def _state(self, gcmd):
        value = gcmd.get("NAME", None)
        state = "" if value is None else str(value).strip().upper()
        if not state:
            self._warn(gcmd, "_CONTEXT_STATE requires a non-empty state")
            return None
        return state

    def _flag(self, gcmd, name):
        value = gcmd.get(name, "0")
        normalized = str(value).strip().lower()
        if normalized in ("0", "false", "no", "off"):
            return False
        if normalized in ("1", "true", "yes", "on"):
            return True
        self._warn(
            gcmd, "_CONTEXT_STATE %s must be 0 or 1" % (name,))
        return None

    def _active(self, gcmd, operation):
        if self.contexts:
            return self.contexts[-1]
        self._warn(gcmd, "%s requires an active context" % (operation,))
        return None

    def _cancel_decision(self):
        # A non-interruptible frame protects itself and all ordinary child
        # work.  A nested cancelable frame is an explicit domain and may still
        # be selected before the scan reaches that barrier.
        for frame in reversed(self.contexts):
            mode = frame.definition.cancel_mode
            if mode == CANCEL_MODE_CANCELABLE:
                return frame, None
            if mode == CANCEL_MODE_NON_INTERRUPTIBLE:
                return None, frame
        # With no explicit domain, cooperative interruption aborts the whole
        # current G-code operation tree, represented by its outermost frame.
        if self.contexts:
            return self.contexts[0], None
        return None, None

    def _frame_by_id(self, frame_id):
        for frame in self.contexts:
            if frame.frame_id == frame_id:
                return frame
        return None

    def _changed(self):
        self.revision += 1

    def _warn(self, gcmd, message):
        message = "Operation context: %s" % (message,)
        logging.warning("[operation_context] %s", message)
        responder = getattr(gcmd, "respond_raw", None)
        if responder is not None:
            responder("!! %s" % (message,))
        else:
            self.gcode.respond_raw("!! %s" % (message,))

    def _reset(self, reason):
        if not self.contexts and self.pending_cancel is None:
            return False
        count = len(self.contexts)
        self.contexts = []
        self.pending_cancel = None
        self.cancelling = False
        self._changed()
        logging.warning(
            "[operation_context] reset %d context(s): %s", count, reason)
        return True

    def _handle_interrupted_operation(self, *args):
        del args
        self._reset("G-code lifecycle interruption")

    def _run_cleanup(self, frame, gcmd):
        cleanup = frame.definition.on_cancel
        if not cleanup:
            return
        try:
            self.gcode.run_script_from_command(cleanup)
        except Exception as exc:
            self._warn(
                gcmd, "cleanup for '%s' failed: %s" % (
                    frame.definition.name, exc))

    def _abort_cancelled_operation(self, gcmd, target):
        target_index = self.contexts.index(target)
        frames = list(reversed(self.contexts[target_index:]))
        del self.contexts[target_index:]
        self.pending_cancel = None
        self.cancelling = True
        self._changed()
        try:
            for frame in frames:
                self._run_cleanup(frame, gcmd)
        finally:
            self.cancelling = False
        raise gcmd.error(
            "Operation cancelled: %s" % (target.definition.name,))

    def cmd_CONTEXT_BEGIN(self, gcmd):
        self.cancellation_point(gcmd)
        definition = self._type(gcmd)
        if definition is None:
            return
        frame = ContextFrame(self.next_frame_id, definition)
        self.next_frame_id += 1
        self.contexts.append(frame)
        self._changed()

    def cmd_CONTEXT_STATE(self, gcmd):
        frame = self._active(gcmd, "_CONTEXT_STATE")
        if frame is None:
            return
        self.cancellation_point(gcmd)
        restore = self._flag(gcmd, "RESTORE")
        temporary = self._flag(gcmd, "TEMPORARY")
        if restore is None or temporary is None:
            return
        if restore and temporary:
            self._warn(
                gcmd, "_CONTEXT_STATE cannot combine RESTORE and TEMPORARY")
            return
        if restore:
            if not frame.saved_states:
                self._warn(
                    gcmd, "_CONTEXT_STATE RESTORE requires a temporary state")
                return
            frame.current_state = frame.saved_states.pop()
            self._changed()
            return
        state = self._state(gcmd)
        if state is None or state == frame.current_state:
            return
        if temporary:
            frame.saved_states.append(frame.current_state)
        frame.current_state = state
        self._changed()

    def cmd_CONTEXT_END(self, gcmd):
        frame = self._active(gcmd, "_CONTEXT_END")
        if frame is None:
            return
        self.cancellation_point(gcmd)
        if frame.saved_states:
            self._warn(
                gcmd, "ending '%s' with %d temporary state(s); discarding "
                "only this context's local state" % (
                    frame.definition.name, len(frame.saved_states)))
        self.contexts.pop()
        self._changed()

    def cmd_CONTEXT_CANCEL(self, gcmd):
        result = self.request_cancel()
        if not result["accepted"]:
            if result["status"] == "non_interruptible":
                self._warn(
                    gcmd, "'%s' cannot be interrupted safely" % (
                        result["blocker_name"],))
            else:
                self._warn(
                    gcmd, "active operation cannot be cancelled safely")
            return
        gcmd.respond_raw(
            "// Operation cancellation requested: %s"
            % (result["target_name"],))

    def cmd_CONTEXT_CANCEL_POINT(self, gcmd):
        if self._active(gcmd, "_CONTEXT_CANCEL_POINT") is None:
            return
        self.cancellation_point(gcmd)

    def cmd_CONTEXT_RESET(self, gcmd):
        del gcmd
        self._reset("manual _CONTEXT_RESET")


def load_config(config):
    return OperationContextManager(config)
