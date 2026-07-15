# Backwards-compatible alias for native printer binaries. New build commands
# should use toolchains/printer-eabi.cmake explicitly so it cannot be confused
# with the hard-float chroot toolchain.
include("${CMAKE_CURRENT_LIST_DIR}/toolchains/printer-eabi.cmake")
