# Installed boot guard compatibility and stock restoration

## Scope and evidence

The user supplied an early-boot log containing empty `touch` arguments, a USB
firmware image handoff that killed `init_boot_flag.sh`, concurrent data-partition
mount attempts, continued normal initialization, and another empty readiness
`touch`. This review uses repository history and offline shell harnesses. The
actual installed guard and the later stock-restore logs were not read from the
printer. No device connection or deployment was performed.

## Historical compatibility failures

The pre-Recovery guard sourced `INIT_FLAG`, `BOOT_SKIP_F`, and `BOOT_FAILURE_F`
from `common.sh`. Commit `507daf3b` moved their definitions into `boot_mode.sh`
without preserving that interface for separately installed old guards. The
definitions are already absent in the parent of tuning commit `2bb9f47c`.
Executing historical common scripts confirms the difference. The proposed extra
compatibility export was later rejected: supported older installations use their
existing update paths, and the broken intermediate INIT was rolled back.

The guard before `b30c23a7` also did not export `FIRMWARE_INSTALL_PARENT_PID`.
The new image installer falls back to its immediate parent PID when that value
is absent. In the old guard's call chain, this targets `init_boot_flag.sh`, rather
than the stock boot launcher. Executing the historical guard function and the
current installer stop operation with `kill` intercepted confirms the wrong
target. Returning status 137 from the interrupted handler then lets that old
guard continue normal initialization. The guard after `b30c23a7` passes the PID
and does not select its own handler as the target.

These historical harness results reproduce symptoms like those in the supplied
log, including overlapping `/data` setup. They are not evidence that an old guard
without its normal update path exists in a supported installation. The log does not establish the exact
installed revision or prove which process produced each interleaved mount line.

The current `init-main.sh` stops normal initialization when the special handler
returns 137. A regression test executes that behavior and verifies that normal
data mounting is not reached. This does not establish compatibility with every
older installed guard.

## A separate defect present in the current version

`zdisplay.sh stock` ignored the result of `display_stock`, called `sync`, and
exited successfully. When configuration restoration failed, `S55boot` therefore
received success and wrote `Stock display configuration is ready.` The unchecked
command predates tuning and remains in its parent revision.

The correction propagates the actual restore exit status immediately. Failed
restoration also cannot schedule a reboot through this command. The existing
S55boot failure branch reports the error and retains its stock mode. Its existing
policy of continuing stock startup after a reported restoration failure remains
unchanged; this correction does not repair arbitrary filesystem/chroot/config
failures or guarantee a working stock screen when restoration cannot complete.

Tests execute both the actual display command and the S55boot path with the
actual display script. Before the correction, they reproduce exit status zero
and the false ready message. Existing S55boot tests used a display stub that
correctly returned failures and therefore missed this command boundary.

The final full public suite ran 2608 tests in 148.378 seconds. Its only failure
was the previously recorded `settings.list` golden mismatch, with one skip.
The initial sandbox run additionally blocked 19 local socket tests; an authorized
local rerun removed those environment errors. Whitespace checks pass.

The supplied log stops before S55boot stock preparation. It cannot establish
whether restoration was attempted, failed, timed out, or succeeded on the printer.
The pertinent retained log is `/opt/config/mod_data/log/skip.log`.

## Recovery tuning state contract

- The unchanged guard consumes `BOOT_FLAG_SKIP` to route one boot into Recovery.
  Recovery publishes another request only while profilectl's candidate is
  unresolved, preserving the existing Recovery-failure precedence and stock escape.
- The candidate itself is the pending decision; there is no separate session
  marker. Opening pages or returning to the main menu changes nothing.
- Each tuning entry re-observes the loaded profile and current boot. An expired
  one-shot is reported as fallback and never re-armed automatically.
- Test evidence remains bound to its profile and boot.
- No tuning routing, retry loop, or cleanup belongs in S00init or init-main.sh.
  See [Recovery tuning contract](recovery-tuning-contract.md).

## Final integration boundary

The user rejected the hypothetical old-INIT compatibility layer. common.sh has no
added boot-variable/PID exports, S99root has no guard-updater call, and the extra
update_guard.sh helper is removed. The existing guard self-promotion and supported
update paths remain unchanged. S00init and init-main.sh match the pre-tuning body.

Tuning decisions, results and continuation live in Recovery.
Firmware download/install/removal do not touch tuning or restore stock hardware
implicitly. Pending tuning gates Recovery actions and automatic firmware dispatch.
The proposed emergency USB skip scan was withdrawn; existing priority is unchanged.

The retained zdisplay status correction is independent of this simplification.
Offline validation for the current tree is recorded in the feature plan; historical
suite results above are evidence for the earlier incident investigation only.
Actual-device acceptance is pending and requires separate permission.
