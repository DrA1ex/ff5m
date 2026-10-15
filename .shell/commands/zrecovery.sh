#!/bin/bash
## Enter the Forge-X Recovery menu on the next boot.
set -e
rm -f /opt/config/mod/BOOT_FLASH_IMAGE
touch /opt/config/mod/BOOT_FLAG_SKIP
sync
reboot
