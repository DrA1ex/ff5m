# Native utilities

The sources in this directory build the small native executables shipped in
`.bin/exec`. The printer uses 32-bit ARM Linux binaries; a normal macOS or x86
Linux executable must never be copied to the printer.

## macOS cross-toolchain

### Environment setup

```shell
# Install Xcode command-line tools
xcode-select --install

# Install brew https://brew.sh/
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Install packages
brew install crosstool-ng

# Create case-sensitive volume
hdiutil create ~/crosstool.dmg -volname "x-tools" -size 15g -fs "Case-sensitive APFS"

# Mount and link
hdiutil mount ~/crosstool.dmg
ln -s /Volumes/x-tools ~/x-tools

# Configure and build cross-tools 
ct-ng arm-unknown-linux-gnueabi

# Generic configuration (should already be set, but may be useful for other toolchains)
# - Target architecture: ARM
# - Use EABI
# - Floating point: Software (no FPU)
# 
# Or if you want to build the toolchain to run in Buildroot env (integrated in the mod):
# - [Change] Floating point: Hardware (FPU)
# 
# Libraries:
# - Set glibc version to 2.25.
# - Use Linux Kernel version 5.3.18.
#   (Note: The printer actually uses version 5.4, but do not use versions newer than 5.4.289 as they may be incompatible.)
# - [Optional] You can set GCC version to 7.5.0. 
#   (However, keep in mind that GCC 7.5.0 is pretty old.)

# Launching configuration tool:
ct-ng menuconfig

# Build (this takes a long time on the first run)
ct-ng build -j"$(sysctl -n hw.ncpu)"

# OR rebuild changes only:
# ct-ng build.2 -j"$(sysctl -n hw.ncpu)"
```

## Two ARM ABIs

Forge-X uses two incompatible ARM environments:

| Environment | Toolchain | ELF loader | Intended use |
|---|---|---|---|
| Native printer firmware | `arm-unknown-linux-gnueabi` | `/lib/ld-linux.so.3` | `typer`, `boot_mcu`, `logged`, and other programs started outside the chroot |
| Buildroot chroot | `arm-unknown-linux-gnueabihf` | `/lib/ld-linux-armhf.so.3` | Programs that run exclusively inside the hard-float chroot |

Never reuse one CMake build directory for both ABIs and never copy an `eabihf`
binary over a native printer executable. CMake caches the selected compiler; a
later `-DCMAKE_TOOLCHAIN_FILE=...` does not safely convert an existing build
directory.

The explicit toolchain files are:

- `.bin/src/toolchains/printer-eabi.cmake`
- `.bin/src/toolchains/chroot-eabihf.cmake`

The old `.bin/src/toolchain.cmake` remains as an alias for `printer-eabi.cmake`.
The checked-in toolchain files expect compilers under:

```text
/Volumes/x-tools/arm-unknown-linux-gnueabi/bin/
/Volumes/x-tools/arm-unknown-linux-gnueabihf/bin/
```

If the toolchains are installed elsewhere, update `TOOLCHAIN_ROOT` in the
corresponding file locally before configuring CMake.

## Building `typer` for the printer

Run these commands from the repository root:

```shell
cmake \
  -S .bin/src/typer \
  -B .bin/src/typer/cmake-build-printer-eabi \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_TOOLCHAIN_FILE="$PWD/.bin/src/toolchains/printer-eabi.cmake" \
  -DBUILD_TESTING=OFF

cmake --build .bin/src/typer/cmake-build-printer-eabi --parallel
file .bin/exec/typer
```

The last command must report an ARM Linux executable. The resulting binary is
`.bin/exec/typer`, which is the path used by the firmware package.

Every `typer --double-buffered` (`-db`) invocation first tries to use the second
800×480 page already exposed by `/dev/fb0` as its backbuffer. This applies both
to Feather pipe mode and to standalone commands such as those in `screen.sh`.
The visible page is not switched: `flush` keeps the established behavior of
copying only the dirty rectangle. If the framebuffer geometry or mapping does
not support a second page, `typer` logs the reason to stderr and falls back to a
1,536,000-byte heap backbuffer. A non-blocking process lock protects the shared
hardware page: a concurrent `-db` process logs that it is busy and uses heap
memory for its own lifetime. Running without `-db` remains single-buffered.
On the printer's two-page 800×480 framebuffer this removes the persistent
1,536,000-byte anonymous allocation; RSS still includes reclaimable executable
and shared-library pages.

To rebuild after source changes:

```shell
cmake --build .bin/src/typer/cmake-build-printer-eabi \
  --parallel --clean-first
```

`typer` has an additional configure-time guard that rejects the `gnueabihf`
compiler. To validate the finished ELF more precisely:

```shell
/Volumes/x-tools/arm-unknown-linux-gnueabi/bin/arm-unknown-linux-gnueabi-readelf \
  -l .bin/exec/typer | grep 'program interpreter'

/Volumes/x-tools/arm-unknown-linux-gnueabi/bin/arm-unknown-linux-gnueabi-readelf \
  -h .bin/exec/typer | grep 'Flags:'
```

Expected values are `/lib/ld-linux.so.3` and `soft-float ABI`. A hard-float
binary will not run in the native printer environment merely because it is also
ARM EABI5.

For another native project, replace `typer` with its directory name. Configure
each project in a separate `cmake-build-*` directory.

## Running host tests

The interactive touch parser and hitbox registry are platform-independent. They
can be compiled and tested locally without the ARM toolchain:

```shell
cmake \
  -S .bin/src/typer \
  -B .bin/src/typer/cmake-build-host \
  -DCMAKE_BUILD_TYPE=Debug \
  -DCMAKE_RUNTIME_OUTPUT_DIRECTORY="$PWD/.bin/src/typer/cmake-build-host/bin" \
  -DBUILD_TESTING=ON

cmake --build .bin/src/typer/cmake-build-host \
  --target typer_tests --parallel

ctest --test-dir .bin/src/typer/cmake-build-host \
  --output-on-failure --no-tests=error
```

CTest registers each scenario separately, so failures include the affected case
name instead of only the test executable. The suite covers:

- hitbox boundaries, overlap precedence, negative coordinates, and clearing;
- touch press/release sequencing, coordinate ordering, duplicate release, and
  the tap-versus-swipe threshold;
- batch tokenization, empty/quoted/escaped/Unicode text, embedded NUL separators,
  and malformed input;
- the shared Feather draw-frame fixture, including Python escaping, C++
  tokenization, and `--batch` command framing;
- end-to-end draw FIFO framing across fragmented writes and touchscreen events
  emitted as `tap <action-id>` through the event FIFO;
- framebuffer page selection, validation, and heap fallback;
- external and owned backbuffer initialization, dirty flushes, switching, and
  lifetime safety.

To stress the asynchronous FIFO test locally:

```shell
ctest --test-dir .bin/src/typer/cmake-build-host \
  -R typer_fifo_integration_test \
  --repeat until-fail:20 \
  --output-on-failure
```

### Feather Python tests

The Feather tests use only Python's standard library. Run the UI, workflow,
and patched G-code modules from the repository root:

```shell
PYTHONDONTWRITEBYTECODE=1 \
  python3 -m unittest \
    tests/test_feather_screen.py \
    tests/test_feather_joystick.py \
    tests/test_feather_workflows.py \
    tests/test_gcode_patch.py \
    tests/test_network_scripts.py \
    -v
```

To discover and run every Python test under `tests/`:

```shell
PYTHONDONTWRITEBYTECODE=1 \
  python3 -m unittest discover -s tests -v
```

The suite checks page routing, stale and repeated actions, button states, footer
layout, ECO wake behavior, heater limits, filament safety, first-layer Z limits,
bed-screw parsing, mesh commands, file/path safety, print transitions, fragmented
C++ tap events, network credentials/timeouts, recovery status, and immediate
G-code command serialization. They also cover mod-parameter switches, option and
value editors, scrolling, atomic splitting of large draw frames, continuous
touch delivery, joystick inertia, and boundary braking. Network tests verify
transactional interface switching, persisted boot mode, DNS cleanup, and shell
syntax.

Syntax-check the Klipper plugins separately:

```shell
python3 -m py_compile \
  .py/klipper/plugins/feather_joystick.py \
  .py/klipper/plugins/feather_mod_settings.py \
  .py/klipper/plugins/feather_ui.py \
  .py/klipper/plugins/feather_screen.py \
  .py/klipper/plugins/mod_params.py \
  .py/klipper/plugins/resurrection.py
```

Check the related shell scripts from the repository root:

```shell
bash -n \
  .root/S35tslib \
  .root/start.sh \
  .shell/network_common.sh \
  .shell/boot/boot.sh \
  .shell/commands/zdisplay.sh \
  .shell/commands/znetwork.sh

sh -n \
  .root/S35tslib \
  .root/start.sh \
  .shell/network_common.sh \
  .shell/commands/zdisplay.sh \
  .shell/commands/znetwork.sh

git diff --check
```

## Installing a development build

Prefer the normal Forge-X packaging/update workflow. For a controlled
development test, first verify the architecture with `file`, then copy to a
temporary path instead of replacing the running executable immediately:

```shell
scp -O .bin/exec/typer root@<printer_ip>:/tmp/typer.new
ssh root@<printer_ip> 'file /tmp/typer.new && chmod 755 /tmp/typer.new'
```

Replacing `/root/printer_data/bin/typer` affects the live Feather display and
should only be done while the printer is idle, with the old binary backed up.

## Setting up a new native project

```shell
PROJECT=<project>

mkdir ".bin/src/$PROJECT"

# Create and configure CMakeLists.txt
touch ".bin/src/$PROJECT/CMakeLists.txt"
# Fill CMakeLists.txt with actual project configuration

# Configure it in an out-of-tree build directory
cmake \
  -S ".bin/src/$PROJECT" \
  -B ".bin/src/$PROJECT/cmake-build-printer-eabi" \
  -DCMAKE_TOOLCHAIN_FILE="$PWD/.bin/src/toolchains/printer-eabi.cmake"

cmake --build ".bin/src/$PROJECT/cmake-build-printer-eabi" --parallel
```
