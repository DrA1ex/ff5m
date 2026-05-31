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

| algo | ratio (real pages) | CPU |
|---|---|---|
| `zstd` (default) | ~4× | moderate |
| `lzo-rle` | ~2.7× | low |
| `lzo` | ~2.7× | lowest |

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
