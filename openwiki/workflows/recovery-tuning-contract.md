# Recovery hardware tuning contract

Status: native profile control implemented and validated offline, 2026-11-15. Hardware acceptance requires
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
locking, U-Boot selection, promotion and rollback. The C++20 implementation lives
in `forge-x-private/src/overclocking/src/profilectl`; FF5M owns the manifest,
runtime data and Recovery integration. Each repository tests its side of
the public protocol. The native program at `.bin/tools/overclocking/bin/t113-profilectl` is covered by
`LICENSE-BINARIES.md`. No shell wrapper or shell implementation is shipped.
`fgx-fdt`, `fgx-lock` and `fgx-stress` remain separate native helpers.
`publish.sh --overclocking-only --public-root /path/to/ff5m` publishes the ARM
program and helpers and updates their bundle checksum entries, preserving public data.

FF5M tests the Recovery client through mocked operations, fixture executables and
reviewed protocol examples under `tests/fixtures/profilectl-protocol`. These tests
exercise the real parser, manifest loader, process handling and Recovery decisions;
they need no host profilectl build or private checkout:

```sh
.venv/bin/python -m unittest tests.test_overclocking_profiles \
  tests.test_overclocking_ui -q -b
```

`forge-x-private/src/overclocking` owns the real executable's behavior and public
producer contracts: CLI, snapshot/progress output, transactions, fault recovery,
hardware detection, and state and fault sweeps. Its CTest suite and
`Overclocking contracts` CI build every native dependency. Its test data lives
there, so neither a public checkout nor its Git history is required. Missing
dependencies and unexpectedly skipped contracts fail that suite. Python remains
the subprocess and filesystem test harness.

The protocol examples have independent copies on each side: private compares real
output against their required fields; FF5M feeds them to the real client. They are
reviewed examples, not a complete frozen output schema. Additive fields remain
allowed. Update both copies deliberately for a contract change; never regenerate
expected results automatically from the implementation under test.

Use `build.sh` in private for the printer Release build. It loads libstdc++ from
Forge-X's `/opt/lib/14.2.0` (RPATH), which `init-main.sh` links to
`.bin/runtime/14.2.0`; Recovery's display (Typer) already depends on that runtime.
The build favors size over speed. This test ownership change does not alter the
published ARM utilities or runtime behavior.

## The pending decision

An unresolved trial is exactly "profilectl's `candidate` exists". There is no
separate session, marker or supervisor. Opening HARDWARE TUNING changes nothing;
apply, accept, discard and restore are explicit profilectl operations. Discard
and RESTORE STOCK remove `candidate` right after the fallback boot is durable, so
an interrupted discard leaves ordinary cleanup, not a pending trial.

Extra RAM defaults to `off` in old metadata and preserves CPU and eMMC settings.
Profilectl checks the stock kernel, DTB and both OP-TEE copies before arming or
accepting an enabled profile. The enabled boot path skips `boot_dsp0` and keeps
the OP-TEE reservation. The one-shot restores the exact previous boot command
before trying the candidate. Verification checks live reservations and the
expected change in `MemTotal`. Ordinary boots skip the compatibility check.

Old metadata defaults to eMMC mode `normal`. Trials of other settings preserve
the eMMC setting of the confirmed profile, including a marked one such as DDR52.

### Tuning manifest

`manifest-v2.json` holds the tuning settings and nothing else profilectl owns:
`cpu_setting` rows in `cpu`; `emmc_family`, `emmc_setting` and optional
`emmc_restriction` rows in `emmc`; and `canonical_stock.sha256`, which Recovery
compares with the reported stock DTB hash. Boot addresses, slots, thermal trips,
voltages and tool versions are code in profilectl and fgx-fdt. profilectl parses
the JSON once per run into typed records. Objects, arrays, strings and non-negative
32-bit integers are accepted; duplicate/unknown keys, wrong types, fractions,
overflow, control characters, BOM, incomplete input and trailing data are refused.
Only escaped quotes and backslashes are supported. Nesting is bounded to protect
the loader; record layout and whitespace do not matter. A broken manifest makes
bundle validation and every mutation fail before state or boot writes, and
read-only commands then report no settings and an unknown eMMC family. A bundle
whose files only fail their checksums still reports its settings. Semantic
validation keeps CPU aliases, recipe keys, bus modes, stock rows, family references
and model restrictions unambiguous. It also requires at least one CPU setting and
a `gain` such as `2x` or `1.5x` on every eMMC setting, because Recovery labels
settings by gain and name. Bundle validation then builds and verifies
every CPU × eMMC row × Extra RAM state with fgx-fdt.

A `cpu_setting` row has `alias` and `accepted_aliases` (CLI values of `--apply
test cpu`), `exact_mhz` and an optional `warning` that Recovery shows as
EXPERIMENTAL. `exact_mhz` is the profile id saved as `cpu_profile` and passed to
fgx-fdt, which owns the OPP recipe of each profile and refuses any other. `stock`
is not a row: it is U-Boot's 1008 MHz boot clock.

### eMMC settings

`manifest-v2.json` describes every eMMC setting as an `emmc_setting` row in its
`emmc` array; nothing else in profilectl, fgx-fdt or Recovery names a setting.
A row is keyed by `(family_id, mhz, mode)`, the same values saved profile metadata
stores as `family`, `emmc_mhz` and `emmc_mode`, so a row change never rewrites
metadata. Fields:

| Field | Meaning |
| --- | --- |
| `id`, `aliases` | CLI values of `--apply test emmc`; unique within the family. |
| `mhz` | Generated `max-frequency`: 25, 50, 100 or 150. |
| `mode` | `normal`, or a token written as the `forge-x,emmc-mode` marker. |
| `remove` | Capabilities removed from the eMMC node: `mmc-hs200-1_8v`, `mmc-ddr-1_8v`. |
| `timing`, `voltage` | Accepted `ios` timing and signal voltage after boot. |
| `gain`, `name`, `note` | Recovery label (`2x  //  50 MHz HS52`) and confirmation note. |

Each family has exactly one `stock` row at 25 MHz `normal`. Rows of a family
appear in display order. fgx-fdt applies a row's recipe exactly (`--emmc`,
`--family`, `--emmc-remove`, `--emmc-mode`) and only within fixed limits: the
four clocks, the two removable capabilities and a `[a-z0-9]{1,16}` mode token.
Verification requires the same recipe. Bundle validation refuses ambiguous rows
(repeated ids, aliases, recipe keys or family bus modes, an unknown family, a
family without its stock row) and then builds and verifies every eMMC row with
every CPU profile and both Extra RAM states, so fgx-fdt refuses a recipe outside
its limits there. Removing a row that saved profiles use makes those profiles
invalid; keep such rows.

Current rows: HS200 eMMC offers stock, HS52, 50, DDR52, 100 and 150; legacy eMMC
offers stock, HS52 (`50`) and DDR52. HS52 on HS200 eMMC removes both 1.8 V
capabilities and negotiates timing 1; DDR52 removes `mmc-hs200-1_8v` and
negotiates timing 8. profilectl verifies the clock, negotiated timing and
reported voltage against the row; a mismatch fails as `<name> runtime mode
mismatch`. Adding a setting inside those limits is a manifest row plus
`SHA256SUMS`.

Snapshot publishes `available_emmc_settings` (row ids in display order, after
family and model restrictions), `<profile>_emmc_setting` and the verdicts
`confirmed_active` and `candidate_active`. A profile is active when the loaded
DTB provenance matches it where readable, its row's marker and bus mode match
(a normal row is not active under a marker or a timing only marked rows
negotiate), Extra RAM matches and the observed CPU and eMMC clocks equal it.
Recovery renders these values and the row labels; it does not decide
eligibility or which profile runs.

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

Read-only commands, including `doctor`, own any temporary boot mount in the invoking
process and release it on success or failure. An existing mount belongs to its
original caller and is never unmounted or remounted automatically. Verification
and diagnostic reports write only profile state under `/data`, so a read-only boot
mount is sufficient and no boot files are removed. Accepting or cancelling a trial
still needs writable boot access to clean up owned temporary DTBs; cancellation
preserves the stock DTB and does not rewrite an already restored fallback bootcmd.

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

Manifest validator version 11 selects the family from the running MMC
controller, independently of CID or Product Name. `emmc_family` records map the
observed bus mode to a family; a loaded Forge-X DTB with an eMMC mode marker
names its own family when its row expects the observed bus mode, because a
marked setting can look like the other family (HS52 on HS200 eMMC) or negotiate
a mode of either (DDR52):

| Runtime timing | Signal voltage | Family | Frequencies (MHz) |
| --- | --- | --- | --- |
| 9 (HS200) | 1 (1.8 V) | hs2001v8 | 25, 50, 100, 150 |
| 0 or 1 (Legacy / HS52) | 0 (3.3 V) | legacy3v3 | 25, 50 |

Unknown, missing or ambiguous runtime evidence blocks profile changes. Missing,
malformed or unlisted CID does not block a supported mode. The existing explicit
trial and mandatory verification still determine whether a frequency works on
this individual printer.

A family's eligible clocks are the clocks of its `emmc_setting` rows. Optional
`emmc_restriction` rows name `models` as exact `MID:OEM:PNM-hex:PRV` identities
and a `frequencies_mhz` list. Serial, manufacturing date and CRC/end bits never
participate. Every restriction matching the observed family and model intersects
the eligible clocks; a nonmatching restriction does nothing. Restrictions cannot
add clocks or substitute for mode detection. An empty intersection offers no
setting, including no implicit stock entry. For example, append this row to
restrict one HS200 model to 25 and 50 MHz:

```json
{"kind":"emmc_restriction","family_id":"hs2001v8","models":"d6:0103:353841333938:11","frequencies_mhz":"25,50"}
```

To mark a model unsupported, give two matching records with disjoint lists
(for example `25` and `50`): the intersection is empty. Snapshot and `detect`
report `hardware=supported` exactly when the eligible list is not empty, so an
unknown mode and an excluded model are both `unsupported`. Hardware support
gates only new trials; it never replaces tuning with the repair page, and
deciding a pending trial or RESTORE STOCK remains available. An excluded model
refuses only new eMMC trials: a CPU trial keeps the effective eMMC frequency
unchanged. An unknown mode refuses CPU trials too, because every generated DTB
states the eMMC family. Recovery shows the refusal when that frequency page is
opened, and profilectl repeats the check before writing. There is no override
flag.

Manifest rows may use any JSON layout. Update `SHA256SUMS` after changing bundle data. The bundle has no JSON
Schema: profilectl's loader is the only manifest validator, including model keys and that a restriction lists
only clocks of its family. After editing the manifest, run the private suite with `src/test.sh --public-root`
pointing at this checkout; its `overclocking_public_manifest` test loads this manifest with the native loader. The bundle key invalidates old
validation caches; persisted candidate/current metadata and generated DTBs are
unchanged.

profilectl owns the eligibility calculation. `list emmc`, the snapshot field
`available_emmc_settings` and the pre-write trial check use its result. Recovery
uses that snapshot list directly, including restrictions that remove 25 MHz. The eMMC
frequency chooser displays Product Name, runtime mode, voltage and actual clock.
Generic repair/tuning and CPU pages omit the eMMC identity. Unsupported-type
messages identify the observed timing and voltage without describing a model as
having failed validation; only a model excluded by the manifest is named by its
Product Name. The confirmed profile frequency remains separate from
the observed clock.

Each Recovery entry records `Tuning diagnostic` JSON lines in `recovery.log`.
Snapshot fields include the compact model identity, runtime observations, boot
graph/file references, mismatched stock helper names and restore availability.
When the entry observation shows an abnormal boot state, and when RESTORE STOCK
does not reach clean stock, Recovery also runs read-only `doctor`, retaining boot
variable values, expected/actual protected helpers, the active and expected boot
commands, referenced files, missing dependencies, unsafe paths and DTB hashes.
Diagnostic failure is logged without hiding the original repair screen.

`boot_normal` and `boot_recovery` are optional stock variables by explicit
product policy. Their values, dependencies and file references are opaque to
classification. Their presence or absence is harmless; tuning, cleanup and
rollback never validate, delete, restore or create them. Protected `bootcmd` and
the canonical `setargs_mmc` / `boot_dsp0` / `boot_uImage` checks still apply.
Read-only diagnostics can display the optional helpers with normal redaction.

Before the first boot mutation, profilectl saves the complete `fw_printenv`
output in `/data/.forge-x-overclocking/profile/original/environment.txt`, alongside
raw boot and both U-Boot environment partition images. This private immutable
original includes arbitrary vendor variables, empty values and ignored helpers.
It preserves the full values and participates in `original/SHA256SUMS` integrity
checks. A later backup verifies and retains the first original; corruption blocks
overwriting it. Files and the original directory are private to the owner.

Diagnostic archives are shared publicly for support, so output redacts values
that identify a specimen or grant access: a full CID contains the chip serial,
and U-Boot holds the printer serial (`snum`, `serial#`) and MAC addresses. The
printer model and boot commands are not private. profilectl owns redaction of
named values: `doctor`, persistent diagnostic reports and its helper-mismatch
errors redact a value whose name has a sensitive `_`-separated segment (`mac`,
`wifi_mac`, `serial#`, `password`, `key`, ...), so `machine` stays visible.
Boot command structure and harmless parameter references such as `${snum}`
remain useful for comparison. The Recovery client only replaces raw 32-digit
CIDs and MAC addresses that reach any message and strips control characters. Ordinary command streams are
bounded at 4,000 characters; `doctor` stdout allows 16,000. Snapshot fields are
bounded at 256 characters. Existing log rotation and CREATE DIAGNOSTICS include
`recovery.log*`, and both TAR_DEBUG entry points recursively include the entire
`/opt/logFiles/` installation log tree, including hidden and rotated files.
Read-only CLI commands do not alter boot/profile state.

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
