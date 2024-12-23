#!/bin/bash

# "lanCode"
# "printerSerialNumber"
# Adventurer5M.json

if [ $# -ne 2 ] && [ $# -ne 3 ]; then echo "Используйте $0 PRINT|CLOSE FILE [PRECLEAR]"; exit 1; fi

CURL="/opt/cloud/curl-7.55.1-https/bin/curl"
ip=$(ip addr | grep inet | grep wlan0 | awk -F" " '{print $2}'| sed -e 's/\/.*$//')
if [ "$ip" == "" ]; then ip=$(ip addr | grep inet | grep eth0 | awk -F" " '{print $2}'| sed -e 's/\/.*$//'); fi

serialNumber=$(cat /opt/config/Adventurer5M.json | grep "printerSerialNumber"| cut  -d ":" -f2| awk '{print $1}' | sed 's|[",]||g')
checkCode=$(cat /opt/config/Adventurer5M.json | grep "lanCode"| cut  -d ":" -f2| awk '{print $1}' | sed 's|[",]||g')

if [ "$1" == "CLOSE" ]
    then
        $CURL -s \
        http://$ip:8898/control \
        -H 'Content-Type: application/json' \
        -d "{\"serialNumber\":\"$serialNumber\",\"checkCode\":\"$checkCode\",\"payload\":{\"cmd\":\"stateCtrl_cmd\",\"args\":{\"action\":\"setClearPlatform\"}}}"
else
    if [ "$1" == "PRINT" ]
        then
            if [ "$3" == "PRECLEAR" ]
                then
                    M109=$(grep "^M109" "/data/$2" | head -1)
                    [ "$M109" == "" ] && M109=$(grep "^M104" "/data/$2" | head -1 | sed 's|M104|M109|')
                    M190=$(grep "^M190" "/data/$2" | head -1)
                    [ "$M190" == "" ] && M190=$(grep "^M140" "/data/$2" | head -1 | sed 's|M140|M190|')

                    if [ "$M190" != "" ] && [ "$M109" != "" ]
                        then
                            echo "$M190" >/tmp/printer
                            echo "$M109" >/tmp/printer
                            echo "_START_PRECLEAR" >/tmp/printer
                        else
                             echo "RESPOND TYPE=error MSG=\"В файле $2 не найдены команды нагрева стола(M140/M190) или сопла(M104/M109).\"" >/tmp/printer
                    fi
            fi

            grep EXCLUDE_OBJECT_DEFINE "/data/$2" >/tmp/printer 2>/dev/null
            $CURL -s \
                http://$ip:8898/printGcode \
                -H 'Content-Type: application/json' \
                -d "{\"serialNumber\":\"$serialNumber\",\"checkCode\":\"$checkCode\",\"fileName\":\"$2\",\"levelingBeforePrint\":true}'"
        else
            echo "Используйте $0 PRINT|CLOSE FILE [PRECLEAR]"
            exit 1
    fi
fi