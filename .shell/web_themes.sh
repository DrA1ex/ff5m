#!/bin/sh
# Native web theme links; preserve every existing user-owned theme.
# This file may be distributed under the terms of the GNU GPLv3 license.
set -eu
[ "$#" -eq 3 ] || { echo "usage: $0 install|remove BUNDLE CONFIG_DIRECTORY" >&2; exit 2; }
action=$1
bundle=$2
config=$3
case "$action" in install|remove) ;; *) exit 2 ;; esac
for name in .theme .fluidd-theme; do
    source=$bundle/$name
    target=$config/$name
    case "$action" in
        install)
            [ -f "$source/custom.css" ] || { echo "Missing web theme: $source" >&2; exit 1; }
            if [ -e "$target" ] || [ -L "$target" ]; then
                continue
            fi
            ln -s "$source" "$target"
            echo "// Installed native web theme: $name"
            ;;
        remove)
            if [ -L "$target" ] && [ "$(readlink "$target")" = "$source" ]; then
                rm "$target"
            fi
            ;;
    esac
done
