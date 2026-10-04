#!/bin/bash

## Mod's uninstall script
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

source /opt/config/mod/.shell/common.sh
source "$SCRIPTS/klipper_overlay.sh"

revert_klipper_patches() {
    klipper_overlay_clean_links "$KLIPPER_OVERLAY_SRC" "$KLIPPER_OVERLAY_TARGET" remove
}

fail() {
    if [ -n "$1" ]; then echo "$1"; fi

    sync
    sleep 1

    echo "@@ Failed to remove mod. Reboot the printer."
    sync

    sleep 300
    reboot
}

uninstall() {
    if ! mount | grep -q "$MOD/sys"; then
        echo "// Init chroot..."
        init_chroot
        
        mount --bind /opt/config "$MOD"/opt/config
    fi
    
    echo "// Restore config..."
    
    chroot "$MOD" /bin/python3 /opt/config/mod/.py/cfg_backup.py \
        --mode restore \
        --config /opt/config/printer.cfg \
        --no_data \
        --params /opt/config/mod/.cfg/restore.cfg \
        || fail "@@ Failed to restore printer.cfg"
    
    chroot "$MOD" /bin/python3 /opt/config/mod/.py/cfg_backup.py \
        --mode restore \
        --config /opt/config/printer.base.cfg \
        --params /opt/config/mod/.cfg/restore.base.cfg \
        --data /opt/config/mod/.cfg/data.restore.base.cfg \
        || fail "@@ Failed to restore printer.base.cfg"
    
    if [ -f "$MOD_DATA/user.cfg" ]; then
        chroot "$MOD" /bin/python3 "$PY/cfg_backup.py" \
            --mode restore --avoid_writes --no_data \
            --config "$MOD_DATA/user.cfg" \
            --params /opt/config/mod/.cfg/restore.plugins.cfg \
            || fail "@@ Failed to restore user.cfg plugin include"
    fi

    echo "127.0.0.1       localhost" > /etc/hosts
    echo "127.0.1.1       kunos" >> /etc/hosts
    
    echo "// Restore klipper..."
    
    revert_klipper_patches || fail "@@ Failed to restore Klipper overlays"
    rm -f "$KLIPPER_USER_CONFIG"
    
    echo "// Remove mod..."
    
    # Make sure to umount all mounted files
    # In case of accidentally run this script after init
    echo "// Unmount paths..."
    
    mount | grep "/data/.mod" | awk '{print $3}' | xargs -n1 -I {} umount -lf "{}"
    mount | grep " /root/printer_data" | awk '{print $3}' | xargs -n1 -I {} umount -lf "{}"
    umount -lf /root/.oh-my-zsh &> /dev/null

    if mount | grep -q /data/.mod || lsof | grep -q /data/.mod; then
        echo "@@ Found running mod services."
        fail
    fi
    
    echo "// Removing services..."
    
    rm -f /etc/init.d/S00fix
    rm -f /etc/init.d/S00init /etc/init.d/.S00init.*
    rm -f /etc/init.d/S55boot
    rm -f /etc/init.d/S99root
    rm -f /etc/init.d/S99moon
    rm -f /etc/init.d/S98camera
    rm -f /etc/init.d/S98zssh
    rm -f /etc/init.d/K99moon
    rm -f /etc/init.d/K99root
    
    echo "// Removing zsh..."
    rm -rf /root/.profile
    rm -rf /root/.zshrc
    echo "" > /etc/motd
    
    echo "// Removing entware..."
    rm -rf /opt/bin
    rm -rf /opt/etc
    rm -rf /opt/home
    rm -rf /opt/lib
    rm -rf /opt/libexec
    rm -rf /opt/root
    rm -rf /opt/sbin
    rm -rf /opt/share
    rm -rf /opt/tmp
    rm -rf /opt/usr
    rm -rf /opt/var
    
    if [ "$1" != "--soft" ]; then
        echo "// Hard remove step..."
        rm -rf /opt/config/mod_data
        rm -rf /opt/.netd-private
        
        echo "// Removing root access..."
        rm -rf /etc/init.d/S50sshd /etc/init.d/S55date /bin/dropbearmulti /bin/dropbear /bin/dropbearkey /bin/dbclient /bin/scp /usr/libexec/sftp-server /etc/dropbear /etc/init.d/S60dropbear
        
        echo "// Removing Beep util..."
        rm -f /usr/bin/audio /usr/lib/python3.7/site-packages/audio.py /usr/bin/audio_midi.sh
        # Remove this nonstock extra even after generic .bak restoration.
        klipper_overlay_clear_user_cache "$KLIPPER_OVERLAY_TARGET/extras/gcode_shell_command.py"
        rm -f "$KLIPPER_OVERLAY_TARGET/extras/gcode_shell_command.py"
        rm -rf /usr/lib/python3.7/site-packages/mido/
    else
        echo "// Preserve root..."
        cp /opt/config/mod/.shell/S60dropbear /etc/init.d/S60dropbear
    fi

    echo "// Removing mod files..."
    
    rm -rf /opt/config/mod/
    rm -rf /root/printer_data
    rm -rf /data/.mod

    rm -rf /data/.feather
    rm -rf /data/.firmware
    rm -rf /data/.firmware-runner
    rm -rf /data/forge-x-recovery
    
    echo "// Done!"
    
    sync
    sleep 1

    echo "// Reboot the printer"
    sync
    
    sleep 300
    reboot
}

xzcat /opt/config/mod/uninstall.img.xz > /dev/fb0

mv /data/logFiles/uninstall.1.log /data/logFiles/uninstall.log.2 &> /dev/null
mv /data/logFiles/uninstall.log /data/logFiles/uninstall.log.1   &> /dev/null

uninstall "$1" 2>&1 | logged "/data/logFiles/uninstall.log" --send-to-screen --screen-no-followup --screen-queue 10
