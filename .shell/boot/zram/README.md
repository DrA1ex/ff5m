# zram compressed swap for AD5M (stock 5.4.61 kernel)

Loadable `zram` + `zsmalloc` modules that add **zstd-compressed swap** to the AD5M
without replacing the stock kernel. Measured **~4× compression** on real klippy/
moonraker cold pages — cuts swap I/O off the eMMC (no flash wear) and multiplies
effective RAM under pressure on the 128 MB board.

## Use
Set the SWAP mode to `ZRAM` (mod settings → SWAP) and pick the algorithm
(`zram compression`: `zstd` best ratio, `lzo-rle`/`lzo` cheaper CPU). On boot,
`init_swap.sh` loads the modules here and brings up `/dev/zram0` as the **primary**
swap (priority 100); the eMMC swapfile remains as a low-priority overflow.

## Measured results (live AD5M, dual Cortex-A7, 128 MB)

**Before / after:**

| | swap target | compression | flash wear | swap speed |
|---|---|---|---|---|
| Before (stock MMC swap) | 64 MB eMMC swapfile | none (1.0×) | yes — wears eMMC | flash I/O (slow, stalls) |
| After (ZRAM, zstd) | 256 MB zram + eMMC overflow | ~4× | none (it's RAM) | RAM speed |

Concretely, ~36 MB of cold klippy/moonraker pages that sat **uncompressed on eMMC
flash** compress to **~9 MB in RAM** — same data, ¼ the footprint, zero flash wear.

**Algorithm comparison:**

| algo | ratio (real heap pages) | ratio (code/binary) | compress\* | decompress\* | A7 CPU |
|---|---|---|---|---|---|
| eMMC swapfile (baseline) | 1.0× (uncompressed) | 1.0× | flash-bound | flash-bound | — (flash wear) |
| **zstd** *(default)* | **4.02–4.18×** | 3.54× | ~6.3 MB/s | ~37.5 MB/s | moderate |
| `lzo-rle` | — | 2.74× | ~7.4 MB/s | ~30 MB/s | low |
| `lzo` | — | 2.74× | ~8.6–9.6 MB/s | ~82.8 MB/s | lowest |

\* Throughput is **block-I/O-bound** (timed `dd` through the zram block device), so
treat the MB/s as **relative**, not absolute — real per-page (de)compression is much
faster. Ratios are from `/sys/block/zram0/mm_stat`; real-heap ratios were measured by
migrating live cold pages into zram (`swapoff` the eMMC file). `lz4` is unavailable
(stock kernel lacks `CONFIG_CRYPTO_LZ4`).

**Recommendation:** `zstd` by default — on 128 MB the extra ~30 % ratio (more effective
RAM) outweighs the CPU cost, and swap is intermittent on a printer. Switch to `lzo-rle`
(setting → `zram compression`) if swap-time CPU ever contends with klippy during a print.

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
</content>
