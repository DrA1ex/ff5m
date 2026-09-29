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
A frame parks the head through `pause_resume`, so `print_stats` reports
`paused` while the print still runs. `TimelapsePhase.print_state()` is the one
rule that maps it to the state the user sees: FRAME is printing, and a user
pause is paused. `page_for_print_state()` applies it, and the cancel page
returns there. `locks_print_controls` names the phases in which Pause,
Filament, and live Z are unavailable.
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
kinds, whether the dialog may reach a frozen surface, and whether it stays on
top. A rejected dialog is logged and returns `None`. `_prompt_draft` exists
only to assemble incoming prompt protocol messages; ordinary prompt display
transfers it into a dialog.
Renderers receive that instance explicitly. Ordinary notifications cannot
replace ERROR or TOUCH_UNAVAILABLE. A message may cover a prompt. The touch
warning stays on top: an error that arrives while it is shown opens beneath
it. Closing the covering layer reveals the retained instance. The warning
layer is the only record that the warning is shown. The screen root's
admitted layers remain authoritative for input when an attempted replacement
frame is rejected.

`_show_terminal_error` is the single path for shutdown and disconnect. It
replaces queued output with the error screen, marks that ERROR as terminal,
and freezes output. A terminal ERROR ignores later non-terminal errors from
the interrupted operation. When touch returns, output is refrozen only if a
terminal ERROR remains, so a runtime error revealed under the warning stays
interactive.

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
