# zram compressed swap for AD5M (stock 5.4.61 kernel)

Loadable `zram` + `zsmalloc` modules add an optional compressed RAM-backed swap
 device to the AD5M without replacing the stock kernel.

ZRAM is not additional physical memory. Pages written to `/dev/zram0` are
compressed and stored in the same system RAM that is used by Klipper, Moonraker
and other services. The configured device size is a logical limit for original,
uncompressed pages; physical RAM is allocated dynamically as pages enter zram.

## Intended use

ZRAM may help when the printer runs substantial secondary software that:

- keeps a meaningful amount of cold anonymous memory;
- is used only occasionally;
- is not sensitive to scheduling or page-fault latency;
- would otherwise cause routine writes to the eMMC swap file.

Examples may include optional background services or interfaces that remain
loaded but are rarely active. ZRAM is not recommended as a way to survive sudden
memory spikes in latency-critical Klipper operations.

## Use

Set the SWAP mode to `ZRAM` (mod settings → SWAP) and select the compression
algorithm (`zram compression`: `zstd` for the best ratio, `lzo-rle`/`lzo` for
lower compression CPU cost).

On boot, `init_swap.sh` loads the modules and activates `/dev/zram0` as the
primary swap at priority 100. A 64 MB eMMC swap file remains available at a
lower priority as overflow. The default zram logical size is **64 MB**. It can be
overridden for development tests with `ZRAM_DISKSIZE`, but larger values are not
recommended without workload-specific validation.

> [!CAUTION]
> Do not treat the eMMC swap file as a latency guarantee. When zram fills, or
> when the kernel performs intensive reclaim while filling it, Klipper can still
> suffer long scheduling stalls before or while overflow swap is used.

## Validation results

### Compression microbenchmarks

Early tests on a live AD5M showed that cold Klipper/Moonraker pages can compress
well. Approximately 36 MB of selected cold pages compressed to about 9 MB with
`zstd`. These measurements demonstrate compression potential, but they do not
represent worst-case whole-system latency under active memory pressure.

| algorithm | ratio (selected real heap pages) | ratio (code/binary) | compress\* | decompress\* | A7 CPU cost |
|---|---:|---:|---:|---:|---|
| eMMC swap file | 1.0× | 1.0× | flash-bound | flash-bound | no compression cost |
| `zstd` | 4.02–4.18× | 3.54× | ~6.3 MB/s | ~37.5 MB/s | moderate |
| `lzo-rle` | not measured | 2.74× | ~7.4 MB/s | ~30 MB/s | low |
| `lzo` | not measured | 2.74× | ~8.6–9.6 MB/s | ~82.8 MB/s | lowest |

\* Throughput was measured with block I/O through the zram device. Treat these
values as relative compressor comparisons, not as a guarantee that Klipper will
remain responsive while the kernel is reclaiming memory. Ratios were read from
`/sys/block/zram0/mm_stat`. `lz4` is unavailable because the stock kernel lacks
`CONFIG_CRYPTO_LZ4`.

### ZSHAPER memory stress tests

System-level memory stress testing was performed with the ZSHAPER workload,
which creates a sharp memory-demand spike while Klipper must continue feeding
the MCU on time. ZRAM logical sizes of **128 MB, 64 MB and 32 MB** were tested.
None of these configurations met the expected stability and latency goals.

| zram size | result |
|---:|---|
| 128 MB | Did not meet expectations under heavy memory pressure. The RAM-backed swap consumed part of the already limited physical memory and did not provide predictable Klipper latency. |
| 64 MB | Did not meet expectations. ZRAM approached capacity, eMMC overflow also became active, and the system entered severe reclaim/swap stalls. |
| 32 MB | Did not meet expectations. ZRAM filled rapidly, tens of megabytes moved to eMMC overflow, and multi-second scheduling gaps were observed. Klipper could fail with `Timer too close`. |

The compressors themselves continued to store data successfully and achieved
roughly 2:1 effective compression in the observed LZO runs. The failure mode was
not corrupted zram data. It was a whole-system latency collapse caused by a
combination of reclaim, compression/decompression, page faults and eMMC swap I/O
on a dual-core system with about 110 MB of usable RAM.

Reducing the zram device from 128 MB to 64 MB or 32 MB changed when the slowdown
occurred, but did not remove it. Lowering `vm.swappiness` and increasing
`vm.page-cluster` also changed the reclaim pattern without making the ZSHAPER
workload reliable: reclaim happened later and in a larger burst.

## Default-size rationale

The original validation demonstrated that approximately **36 MB**
of selected cold Klipper/Moonraker pages could compress well. It did not fill a
256 MB zram device or validate worst-case whole-system latency during ZSHAPER.

The configured zram size limits the amount of **original, uncompressed** memory
that swap can place in zram. A 64 MB device can hold at most about 64 MB of
original pages. If those pages compress 2:1, they consume roughly 32 MB of
physical RAM plus allocator overhead; if they compress 4:1, roughly 16 MB. A
larger logical device does not create more RAM and can allow zram to consume
more of the same limited physical memory before the lower-priority eMMC swap is
used.

The **64 MB default** was selected because it:

- preserves the contributor's documented approximately 36 MB cold-page use
  case with additional logical headroom;
- limits the worst-case amount of physical RAM that zram can consume compared
  with 96 MB, 128 MB or 256 MB defaults;
- moves additional pressure to the eMMC overflow earlier instead of extending
  the compression/reclaim phase;
- remains conservative for the smallest supported memory configuration.

A 96 MB default would provide more logical zram capacity, but no published test
showed that the extra 32 MB improves real workloads or preserves Klipper timing.
On the observed LZO workload, effective compression including allocator overhead
was approximately 1.9:1, so a full 96 MB device could consume roughly 50 MB of
physical RAM. That is too large to use as the project-wide default on systems
with about 110 MB usable RAM.

Systems with more physical RAM do not automatically benefit from a larger zram
device. If their workload already fits in RAM, swap remains unused. Users with a
validated workload containing more than 64 MB of cold, latency-insensitive
anonymous memory may override `ZRAM_DISKSIZE` explicitly and test 96 MB or a
larger value. Such sizes are workload-specific experiments, not recommended
defaults.

## Recommendation

- Keep `MMC` as the normal choice for ZSHAPER, resonance calibration and other
  memory-intensive operations that must preserve Klipper/MCU timing.
- Prefer freeing real RAM before those operations: stop optional screens,
  cameras and secondary services where practical.
- Use `ZRAM` only as an optional workload-specific optimization for cold,
  latency-insensitive secondary software.
- Keep the project default at **64 MB**. Treat 96 MB and larger values as explicit
  workload-specific overrides requiring validation.
- The 64 MB default is a neutral compromise for users who explicitly enable
  ZRAM. It is not a claim that ZRAM makes severe memory pressure safe.

## Files
- `zsmalloc.ko`, `zram.ko` — built for the stock kernel, vermagic
  `5.4.61 SMP preempt mod_unload ARMv7 p2v8`. The AD5M kernel is byte-identical
  across stock firmware 2.6.5–5.1.x, so these load on every supported version.
- `swapon_prio` — tiny static helper to `swapon` at an explicit priority
  (busybox `swapon` has no `-p`).

## Rebuilding the modules (GPL — how they were built)

**You must build from the Allwinner T113 (sun8iw20) VENDOR BSP source, not
kernel.org.** Upstream `linux-5.4.61` builds with a matching vermagic but its
`struct page`/mm layout differs from the vendor kernel → loading Oopses. The
vendor BSP source is layout-exact.

```sh
# 1. Vendor BSP kernel source (Allwinner T113, branch 5.4.61)
git clone --depth 1 --branch 5.4.61 \
    https://github.com/iLotusK9/linux_kernel_aw_t113.git linux
cd linux

# 2. A cross toolchain for 5.4 (local GCC 14 is too new). kernel.org crosstool:
#    https://mirrors.edge.kernel.org/pub/tools/crosstool/files/bin/x86_64/9.5.0/
#      x86_64-gcc-9.5.0-nolibc-arm-linux-gnueabi.tar.xz
export CROSS_COMPILE=/path/to/arm-linux-gnueabi-

# 3. Configure with the STOCK kernel .config (pull /proc/config.gz off a printer),
#    then enable the modules:
zcat config.gz > .config
for s in ZSMALLOC ZRAM ZPOOL ZBUD; do sed -i "/CONFIG_$s\b/d" .config; echo "CONFIG_$s=m" >> .config; done

# 4. Suppress the dirty-git "+" vermagic suffix so it matches the stock kernel exactly
: > .scmversion
sed -i '/CONFIG_LOCALVERSION_AUTO/d' .config; echo '# CONFIG_LOCALVERSION_AUTO is not set' >> .config

make ARCH=arm CROSS_COMPILE=$CROSS_COMPILE olddefconfig
make ARCH=arm CROSS_COMPILE=$CROSS_COMPILE -j"$(nproc)" modules_prepare
make ARCH=arm CROSS_COMPILE=$CROSS_COMPILE -j"$(nproc)" mm/zsmalloc.ko drivers/block/zram/zram.ko

# 5. Verify vermagic == "5.4.61 SMP preempt mod_unload ARMv7 p2v8"
arm-linux-gnueabi-objcopy -O binary --only-section=.modinfo mm/zsmalloc.ko /dev/stdout | tr '\0' '\n' | grep vermagic
# strip + copy mm/zsmalloc.ko and drivers/block/zram/zram.ko here.
```

`swapon_prio` is `swapon(2)` with `SWAP_FLAG_PREFER | prio`; build any armv7
static binary from a ~10-line C wrapper (source in the ReForge project).

zstd is already built into the stock kernel (`CONFIG_CRYPTO_ZSTD=y`), so no
compressor module is needed.
