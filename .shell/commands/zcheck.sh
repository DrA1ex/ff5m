#!/bin/bash

## Printer file checksum verification
##
## Thanks Sergei Rozhkov <https://github.com/ghzserg>
## For collecting the checksum of firmware files
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license


source /opt/config/mod/.shell/common.sh

CHECKSUM_ARCHIVE=/opt/config/mod/md5sum.tar.gz
CHECKSUM_TMP_DIR=/data/.tmp
CHECKSUM_ROOT=/

unpack() {
    local file_path=$1
    local archive_copy="$file_path/md5sum.tar.gz"
    local tar_copy="$file_path/md5sum.tar"

    mkdir -p "$file_path"
    rm -f "$file_path"/md5sum.list "$archive_copy" "$tar_copy"

    cp "$CHECKSUM_ARCHIVE" "$archive_copy" || return 1
    gzip -d "$archive_copy" || return 1
    tar -xf "$tar_copy" -C "$file_path/" md5sum.list || return 1
    rm -f "$tar_copy"
}

verify() {
    local actual_checksum checksum_path counter expected_checksum failures
    local file_path full_path link_target total

    if [ ! -f "$CHECKSUM_ARCHIVE" ]; then
        echo "@@ Checksum list file is missing!"
        return 1
    fi

    echo "// Extracting files..."

    unpack "$CHECKSUM_TMP_DIR" || {
        echo "@@ Unable to extract checksum list."
        return 2
    }

    cd "$CHECKSUM_TMP_DIR" || {
        echo "@@ Unable to create temp folder."
        return 2
    }

    echo "// Scanning files for corruption. It might take a while..."

    counter=0
    failures=0
    total=$(wc -l < "./md5sum.list" | awk '{print $1}')
    echo "// Processed ${counter} / ${total}; errors: ${failures}."

    while IFS=' ' read -r expected_checksum file_path; do
        full_path="${CHECKSUM_ROOT%/}/${file_path#./}"
        checksum_path=$full_path

        case "${file_path#./}" in
            opt/klipper/klippy/*)
                if [ -L "$full_path" ]; then
                    link_target=$(readlink "$full_path") || link_target=""
                    case "$link_target" in
                        /opt/config/mod/.py/klipper/patches/*)
                            checksum_path="$full_path.bak"
                        ;;
                    esac
                fi
            ;;
        esac

        if [[ ! -f "$checksum_path" ]]; then
            echo "@@ File $checksum_path missing."
            failures=$((failures+1))
        else
            actual_checksum=$(md5sum "$checksum_path" | awk '{print $1}')
            if [[ "$expected_checksum" == "$actual_checksum" ]]; then
                if [ "$1" == "--verbose" ]; then echo "Checksum OK: $checksum_path"; fi
            else
                echo "@@ Checksum FAILED: $checksum_path"
                failures=$((failures+1))
            fi
        fi

        counter=$((counter+1))
        echo "// Processed ${counter} / ${total}; errors: ${failures}."
    done < "./md5sum.list"

    rm -f ./md5sum.list

    if [ "$failures" -eq 0 ]; then
        echo "// Verification passed: ${counter} files checked."
        return 0
    fi

    echo "@@ Verification failed: ${failures} of ${counter} files are missing or changed."
    return 1
}

tar_archive() {
    echo "// Collecting files. It might take a while..."

    unpack "$CHECKSUM_TMP_DIR"
    cd /

    (awk '{$1="";$0}sub(FS,"")' < "$CHECKSUM_TMP_DIR/md5sum.list") > "$CHECKSUM_TMP_DIR/tar_file.list"

    fname="system_$(date +'%Y%m%d_%H%M%S').tar"
    fpath="/data/.tmp/${fname}"
    tar -chf "$fpath" -T /data/.tmp/tar_file.list &
    pid=$!

    abort_handler() {
        trap SIGINT
        echo "?? Aborted"

        kill "$pid"
        rm -f "$fpath"

        exit 1
    }

    trap "abort_handler" INT

    while kill -0 $pid; do
        size=$(du -h "$fpath" 2> /dev/null | awk '{print $1}')
        if [ -n "$size" ]; then echo "Written: $size..."; fi
        sleep 1
    done

    mv "$fpath" /data/
    echo "// Done! Download the file from \"/data/${fname}\""
}

if [ "$1" == "verify" ]; then
    mv /data/logFiles/verification.log.2 /data/logFiles/verification.log.3  2> /dev/null
    mv /data/logFiles/verification.log.1 /data/logFiles/verification.log.2  2> /dev/null
    mv /data/logFiles/verification.log /data/logFiles/verification.log.1    2> /dev/null

    verify "$2" | logged /data/logFiles/verification.log --send-to-screen --screen-level 0 --screen-no-followup
    exit "${PIPESTATUS[0]}"
elif [ "$1" == "verify-plain" ]; then
    verify "$2"
    exit $?
elif [ "$1" == "tar" ]; then
    tar_archive | logged --no-log --send-to-screen --screen-level 0 --screen-no-followup
else
    echo "Unsupported mode: \"$1\""
    echo "Usage: $0 (verify|verify-plain|tar)"
    exit 1
fi
