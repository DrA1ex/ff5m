#!/bin/sh
#
# Web config
#

unset LD_PRELOAD

MOD=/data/.mod/.zmod

WEB="fluidd"
grep -q "CLIENT=$WEB" /opt/config/mod_data/web.conf && WEB="mainsail"

echo "# Не редактируйте этот файл
# Используйте макрос
#
# WEB

# Веб интерфейс (fluidd|mainsail)
CLIENT=$WEB
" >/opt/config/mod_data/web.conf

sync
chroot $MOD /opt/config/mod/.shell/root/S70httpd restart
