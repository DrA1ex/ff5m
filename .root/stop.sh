#!/bin/sh

# Stop display-specific services
/opt/config/mod/.root/S35tslib stop
/opt/config/mod/.root/S80guppyscreen stop

/opt/config/mod/.root/S65moonraker stop
/opt/config/mod/.root/S70httpd stop
/opt/config/mod/.root/S45ntpd stop

if [ -d /etc/init.d ]; then
    echo "Stoping user services..."
    
    while read -r file; do
        "$file" stop
    done < <(find ./etc/init.d/ -type f -name "S*" | sort -r)

    echo "Done"
fi
