# Recovery CPU/eMMC tuning contract

Status: implemented and validated offline, 2026-10-27. Hardware acceptance requires
separate permission. Product authority lives in
`/Users/akoreshnyak/dev/forge-x-docs/2026-10-26-recovery-overclocking/`.

## Ownership

| Location | Owner / meaning |
| --- | --- |
| `/data/.forge-x-overclocking/profile/` | `t113-profilectl`: originals, confirmed `current` and the trial `candidate`. |
| `/data/.forge-x-overclocking/tests.json` | `TestResults`: the running test, the latest result per kind and the last acceptance. |
| `/data/.forge-x-overclocking/reference/` | Hardware tests: the eMMC read reference and owned temporary files. |
| `/opt/config/mod/BOOT_FLAG_SKIP` | Existing guard: one-shot request for the next Recovery entry. |
| Boot partition `FORGE_X_A.DTB` / `FORGE_X_B.DTB` | profilectl: the actual U-Boot inputs. |

profilectl is the only owner of hardware profiles, backups, DTB generation,
locking, U-Boot selection, promotion and rollback. Its native sources live in
`forge-x-private/src/overclocking`; only built tools are published here.

## The pending decision

An unresolved trial is exactly "profilectl's `candidate` exists". There is no
separate session, marker or supervisor. Opening HARDWARE TUNING changes nothing;
apply, accept, discard and restore are explicit profilectl operations.

- Recovery start: if a candidate exists, Recovery opens tuning first. A UI
  restart inside the same Recovery (`RECOVERY_UI_RESTARTED=1`, for example
  after START SSH) does not reopen it; the menu gates below still apply.
- After tuning draws its first frame, `request_next_recovery(trial_pending())`
  follows the current state: with a candidate it publishes `BOOT_FLAG_SKIP`
  durably before removing `BOOT_FLAG_RECOVERY_FAILURE` / `BOOT_FLAG_FAILURE`,
  so a reset returns to Recovery; without one it removes the request. Accept and
  discard call it again.
- REBOOT with a pending trial writes `recovery-reboot`; `recovery.sh` keeps the
  flags and reboots. Otherwise `reboot` prepares a normal boot as before.
- BOOT OPTIONS, FIRMWARE / RESTORE and BACKUP / RESET open tuning instead while a
  trial is pending. SYSTEM and NETWORK stay available.
- Bounded failure: a failing Recovery UI never re-arms itself (`recovery.sh`
  removes `BOOT_FLAG_SKIP`), so the guard's existing stock escape applies on the
  next boot. A tuning exception lifts the gate and removes the request for this
  Recovery; ordinary actions remain available.
- The U-Boot one-shot boots the candidate once. Every entry re-observes the
  loaded profile; fallback is reported and never retried automatically.
- `BOOT_FLASH_IMAGE` is consumed at Recovery start. With a pending trial the
  installation is postponed with a notice; the verified image stays under
  DOWNLOADED IMAGES. Requests are never replayed.

S00init and init-main.sh contain no tuning logic. Firmware download, install and
removal have no tuning prerequisite and do not reset accepted hardware settings.

## Tests and evidence

Tests are generators in the Recovery UI process: they yield progress (0..1) and
return `{"status", "detail"}`. Cancel is `close()`; the CPU test's `finally`
terminates and reaps `fgx-stress`, eMMC tests remove their owned files. A workload
exception, including the CPU helper deadline, is `failed`, not `cancelled`.
A detail is one line (for example an error) or optional sentences followed by
`Label: value` lines. The result page draws the facts as a table, a value's
` | ` part (a ratio to stock) in its own muted column, and the sentences as small
muted notes; a pass has no sentence. A one-line detail is drawn like a dialog: the
outcome large and colored, the line below it. Tuning confirmations and messages use
the same headline-and-explanation layout (`RecoveryView.dialog(headline=...)`).

`TestResults.start` records the test before it runs; a Recovery that ends without
`finish` reports it as interrupted on the next entry, without guessing the cause.
Results count only for the profile DTB hash and boot ID they tested. Acceptance
records each outcome, including `not-run`; failed and interrupted results stay
visible after acceptance. profilectl's mandatory `verify` cannot be waived.

Workloads: `fgx-stress` runs two threads for 180 s on a shared 32 MiB arena with
independently checked integer, floating-point and memory results; each core checks
the other core's writes. Once the CPU first reaches the profile's target (within 1%
PLL rounding), every later load sample must stay there and no policy limit may drop;
otherwise the result is inconclusive. One `Clock` fact covers both: "held, no
throttling", or the time at target as a percentage that reads 100% only when no
sample was missed. The detail also lists peak and start temperature and the memory
volume checked across cores.

Speeds are informational and never change a verdict. The CPU test first runs a
fixed interpreter loop in a child process, timed by its own CPU time, so the
clock bounds it and screen use or the second core do not; `fgx-stress` is
memory-bound and barely shows a CPU overclock. Before its workers start,
`fgx-stress` reports one core's sequential read speed over its arena
(`read_mibs`, best of five passes). The overall CPU speed is the geometric mean of
the CPU and memory ratios to stock. Ratios use `STOCK_CPU_POINTS`,
`STOCK_MEMORY_READ_MIBS`, `STOCK_EMMC_READ_MIBS` and `STOCK_EMMC_WRITE_MIBS` in
`overclocking_tests.py`, measured on a stock printer with these same methods; a
value without a stock speed (for example memory from an older helper) is shown
without a ratio, and then there is no overall speed.

The eMMC test (`emmc-integrity`) is one run: it reads the 128 MiB reference once
without checking it, timing only the reads (the checksum work caps verification
near 15 MiB/s), then verifies it after cache eviction. Only when the reference
reads back intact does it write a separate owned 128 MiB file, syncing every
8 MiB and timing only the writes and syncs, verify it and remove it. Writing keeps
a 64 MiB free-space reserve. The result is one `Read` and one `Write` fact with
each stage's outcome and speed; a failed write keeps the passed read visible. Reads
that may come from the page cache, or a missing kernel log, make a pass
inconclusive and add a fact; new kernel storage errors fail a test. Profiles
accepted before this change still show their `emmc-read`/`emmc-write` outcomes.

An eMMC trial without a reference writes it as part of the single APPLY
confirmation; its result page appears only when the reference is unusable.

profilectl mounts the boot FAT only as `vfat` with `iocharset=cp936` and refuses
any other existing mount. The stock kernel's default iocharset is spelled `CP936`,
which names no NLS table, so an untyped busybox `mount` falls back to `msdos`.
msdos keeps 8.3 names only: `FORGE_X_A.DTB` became `FORGE_X_.DTB`, Linux still
found it by the long name, U-Boot did not. cp936 keeps lookups case-insensitive,
as U-Boot's are; `utf8` would not.

The AD5M U-Boot (2018.05) has no hush parser: `if` and `&&` are unknown commands,
and every `;` command runs even after a failure. Boot helpers are therefore plain
`;` sequences that first clear the kernel and DTB headers (`mw.l`), so `bootm`
rejects a failed load instead of booting stale DRAM, and end with `reset`.

profilectl reads the active eMMC clock from `/sys/kernel/debug/mmc0/ios`.
Recovery runs before `boot.sh` mounts debugfs, so `recovery.sh` mounts it for
its own lifetime when absent and unmounts only its own mount on exit.

## Screens

Blocking operations replace the buttons with a progress frame first, so stale
taps cannot act. Entry reports five steps: hardware settings, Recovery return
path, interrupted tests, temporary-file cleanup and opening the tuning page.
`TuningOperation` owns a blocking call's current phase and worker lifetime. A
standard executor future carries its result or exception; progress is the latest
phase, without a queue of intermediate updates. Only the calling UI thread draws.
The screen keeps the current phase and activity visible and updates elapsed
seconds even while a filesystem call waits. The final opening step is a static
frame without a timer, because it tracks no operation. Timer updates show elapsed waiting,
not estimated completion. profilectl's `FGX_PROGRESS:` lines supply its command
and phase; cleanup reports directory scanning, the current file and removal count.
Only regular owned temporary files are removed; references and symlinks stay.
Directory access errors propagate instead of being hidden by glob expansion.

An operation failure shows its cause first, then its step, last phase and
activity, so a short error label clips the context rather than the cause.
Operation starts, phase changes, completion times, exceptions and Typer's
stderr go to `/data/logFiles/recovery.log`. A display failure does not interrupt
the work: the UI waits for the worker to finish, logs its outcome and then
reports the display error. Killing profilectl in the middle of a mutation would
be worse than a missing frame; profilectl's own timeout still bounds it. This
change does not add cancellation or a deadline to filesystem operations.

### Hardware identity and diagnostic records

Manifest validator version 6 uses explicit model/revision keys in `hardware.models`:
`MID:OEM:PNM-hex:PRV`. These are the Linux MMC v2+ CID fields at bits 127..120,
119..104, 103..56 and 55..48. The six product-name bytes remain hex in the key;
serial number, manufacture date and CRC/end bits never take part in equality.
The field layout and sysfs word ordering follow Linux's
[`mmc_decode_cid` and CID attribute](https://github.com/torvalds/linux/blob/master/drivers/mmc/core/mmc.c).
An old exact-CID manifest must be replaced with the complete regenerated bundle;
it is not silently reinterpreted. The validator version and bundle key invalidate
old validation caches. Persisted profile metadata is unchanged.

| Model | MID | OEM | Product bytes | PRV | Family |
| --- | --- | --- | --- | --- | --- |
| HS200G | 15 | 0100 | 485332303047 | 00 | hs2001v8 |
| 58A398 | d6 | 0103 | 353841333938 | 11 | hs2001v8 |
| M4XFIU | 15 | 0100 | 4d3458464955 | 00 | legacy3v3 |

These exact revisions preserve the identities encoded in the previous table;
`00` is not a wildcard or evidence about a different revision. The table means
supported model/revision and runtime family, not individually certified samples.
`hardware=known` requires both one matching model and positive runtime family
evidence. Missing evidence remains `untested`; disagreement or ambiguous records
block ordinary mutation. Frequencies and the explicit trial/mandatory verify
workflow are unchanged; recognizing a model does not certify every specimen at
150 MHz.

Each entry records allowlisted `Tuning diagnostic` JSON lines in the existing
Recovery log. A `command` record contains the operation, phase, duration, exit
code, failure category and bounded stdout/stderr tails. Successful warnings are
retained. Snapshot stdout is parsed as protocol data before logging; arbitrary
environment variables, raw boot commands and full CID are omitted. Command output
redacts specimen identifiers and boot-helper values. `snapshot` records include
model support, runtime observations, boot graph names/file references, stock
helper verdict, the observed DTB hash with whether it matches the bundle, and
whether the repair page offers Restore. Observed runtime fields and model/family
conclusions are named separately.

A mutation is not followed by an extra snapshot: the page either reboots, and
the next entry records a fresh snapshot, or observes the new state itself.
Records cap each snapshot value at 256 characters and
each command stream at 4,000 characters plus a truncation marker. Existing log
rotation and CREATE DIAGNOSTICS include `recovery.log*`. No U-Boot dump is
created. Read-only CLI commands do not alter boot/profile
state.

When `verify` rejects a trial for fatal kernel/MMC journal lines, the log and
screen show only the first 20 matches, which usually name the symptom (for
example `Buffer I/O error ... lost async page write`) rather than the host
controller error that caused it. profilectl therefore saves the complete
journal it judged, headed by the trial CPU, eMMC, family and boot ID, to
`diagnostics/verify-dmesg.log` in its state directory and reports the path. Two
previous generations are kept as `.1` and `.2`; dry runs and passing
verifications write nothing. The file is written in the trial boot itself, so on badly failing
storage it may be incomplete. CREATE DIAGNOSTICS includes all three. The existing entry `prepare()` is a separate operation: it can migrate
legacy state and finish a known interrupted promotion before showing the page.

A confirmed-profile mismatch offers BACK, RESTORE STOCK and REBOOT;
BACK never reboots. Stock CPU is U-Boot's 1008 MHz boot clock: stock `vdd-cpu`
never registers, so there is no cpufreq policy and profilectl observes `cpux` in
`/sys/kernel/debug/clk/clk_summary`. Only a managed CPU profile switches the
regulator to `sunxi-pwm-regulator`, which enables cpufreq and its OPP table. The
1008 MHz cooling floor is a separate setting. Restore Stock uses artifacts compatible with the installed
firmware and preserves the immutable originals.
