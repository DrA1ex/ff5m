# Timelapse and dialog state contracts

The `timelapse_state` Klipper object observes the required start-guard, frame,
and pause status. It owns the phase calculation used by Feather, PAUSE,
RESUME, runout, and Moonraker. Missing objects or fields are contract errors,
including during Klipper connection; they must not look like an idle printer.

| Phase | Meaning |
| --- | --- |
| NONE | No timelapse hold or user handoff |
| WAITING | Waiting for the previous timelapse |
| HELD | Virtual SD remains held during preparation |
| FRAME | Capturing and restoring a parked frame |
| FRAME_USER_PAUSE | The user requested pause during that frame |
| USER_PAUSE | Restoration finished; the user pause remains |

`USER_PAUSE` is separate because Resume must stay blocked during restoration
and become available afterward. Ordinary pauses use Klipper's print state.
Feather suppresses the previous-timelapse prompt while waiting; its page owns
the choices. The Klipper prompt remains available to web clients.
All Feather print-cancel requests enter `_request_print_cancel`, which chooses
direct cancellation of a held file or cooperative operation cancellation. Both
paths mark the request pending through `_mark_print_cancel_pending`. CANCEL
PRINT on the wait page is itself the confirmation; elsewhere a held file asks
first.

Each accepted `DialogInstance` owns its content, pagination, painter, allowed
actions, and priority. The per-kind policy lives in one `DialogSpec` entry of
`DIALOGS`: painter, navigation and content-declared actions, priority, covered
kinds, and whether the dialog may reach a frozen surface. A rejected dialog is
logged and returns `None`. `_prompt_draft` exists only to assemble incoming prompt
protocol messages; ordinary prompt display transfers it into a dialog.
Renderers receive that instance explicitly. Ordinary notifications cannot
replace ERROR or TOUCH_UNAVAILABLE. A message may cover a prompt, and a touch
warning may cover an error; closing the covering layer reveals the retained
instance. The screen root's admitted layers remain authoritative for input
when an attempted replacement frame is rejected.

Moonraker owns print finalization through `PrintFinalization`: the previous
filename, frame generation, and cancellable task. Selecting the next file or
changing `_START_PRINT.print_active` cannot cancel that task. The start guard
requests `timelapse_cancel_render` explicitly before continuing. Cancellation
invalidates old commits before awaiting subprocess or task completion and
preserves source frames until the next print starts. An archive worker must
finish before its partial output is removed. A late finalization cannot clear
the newer finalization object. A print that bypassed the start guard while
finalization ran begins once finalization completes: `print_active` is reported
only on change, so Moonraker re-observes it instead of dropping the new print's
frames.

Behavioral coverage includes frame restoration and user-pause handoff, held SD
release/cancellation, missing contract fields, dialog recovery priority,
prompt replacement admission, finalization cancellation, archive worker
cleanup, and stale generation results. Shared UI geometry and framework
behavior remain unchanged. These changes require matching Klipper plugin,
macro, and Moonraker component versions; no live deployment is part of the
refactor.
