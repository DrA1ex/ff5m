#!/bin/bash

## Mod's uninstall script
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

source /opt/config/mod/.shell/common.sh

revert_klipper_patches() {
    local SRC_DIR="/opt/config/mod/.py/klipper"
    local TARGET_DIR="/opt/klipper/klippy"
    
    # Klipper extensions
    echo "Remove klipper plugins: "
    echo $SRC_DIR/plugins/*
    echo
    
    find $SRC_DIR/plugins/ -type f | while read -r file; do
        local rel_file=${file#"$SRC_DIR/plugins/"}
        
        rm -f "$TARGET_DIR/extras/$rel_file"
        
        echo "?? Removed \"$rel_file\""
    done
    
    # Klipper patches
    find $SRC_DIR/patches -type f | while read -r file; do
        local rel_file=${file#"$SRC_DIR/patches/"}
        local target="$TARGET_DIR/$rel_file"
        
        if [ -f "$target.bak" ]; then
            mv -f "$target.bak" "$target"
            echo "?? Restored \"$target\""
        fi
    done

    # Klipper tunning
    "$CMDS"/ztune_klipper.sh 0
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
    
    echo "127.0.0.1       localhost" > /etc/hosts
    echo "127.0.1.1       kunos" >> /etc/hosts
    
    echo "// Restore klipper..."
    
    revert_klipper_patches
    
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
    rm -f /etc/init.d/S00init
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
        rm -rf /etc/init.d/S50sshd /etc/init.d/S55date /bin/dropbearmulti /bin/dropbear /bin/dropbearkey /bin/scp /etc/dropbear /etc/init.d/S60dropbear
        
        echo "// Removing Beep util..."
        rm -f /usr/bin/audio /usr/lib/python3.7/site-packages/audio.py /usr/bin/audio_midi.sh /opt/klipper/klippy/extras/gcode_shell_command.py
        rm -rf /usr/lib/python3.7/site-packages/mido/
    else
        echo "// Preserve root..."
        cp /opt/config/mod/.shell/S60dropbear /etc/init.d/S60dropbear
    fi

    echo "// Removing mod files..."
    
    rm -rf /opt/config/mod/
    rm -rf /root/printer_data
    rm -rf /data/.mod
    
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
