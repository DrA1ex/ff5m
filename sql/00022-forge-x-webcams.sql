-- Add the Forge-X camera and screen stream to the web interface.
-- The entries start hidden: zstreams.sh webcams shows the ones whose streams are
-- enabled once the API is up, and owns their `enabled` flag from then on. Every
-- other field belongs to the user and is never changed by Forge-X.
-- Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
-- This file may be distributed under the terms of the GNU GPLv3 license.

BEGIN TRANSACTION;

-- Moonraker stores database webcams in the `webcams` namespace, keyed by a UUID4.
INSERT INTO namespace_store (namespace, key, value)
SELECT 'webcams',
       printf('%s-%s-4%s-%s%s-%s', lower(hex(randomblob(4))), lower(hex(randomblob(2))),
              substr(lower(hex(randomblob(2))), 2), substr('89ab', 1 + abs(random()) % 4, 1),
              substr(lower(hex(randomblob(2))), 2), lower(hex(randomblob(6)))),
       json('{
           "name": "4gx_camera",
           "enabled": false,
           "icon": "mdiWebcam",
           "service": "mjpegstreamer-adaptive",
           "urlStream": "/webcam/?action=stream",
           "urlSnapshot": "/webcam/?action=snapshot",
           "aspectRatio": "4:3",
           "targetFps": 15,
           "targetFpsIdle": 5,
           "location": "printer",
           "flipX": false,
           "flipY": false,
           "rotation": 0
       }')
WHERE NOT EXISTS (
    SELECT 1 FROM namespace_store
    WHERE namespace = 'webcams' AND json_extract(value, '$.name') = '4gx_camera');

INSERT INTO namespace_store (namespace, key, value)
SELECT 'webcams',
       printf('%s-%s-4%s-%s%s-%s', lower(hex(randomblob(4))), lower(hex(randomblob(2))),
              substr(lower(hex(randomblob(2))), 2), substr('89ab', 1 + abs(random()) % 4, 1),
              substr(lower(hex(randomblob(2))), 2), lower(hex(randomblob(6)))),
       json('{
           "name": "4gx_screen",
           "enabled": false,
           "icon": "mdiWebcam",
           "service": "iframe",
           "urlStream": "/screen/",
           "urlSnapshot": "/screen/?action=snapshot",
           "aspectRatio": "5:3",
           "targetFps": 30,
           "targetFpsIdle": 30,
           "location": "printer",
           "flipX": false,
           "flipY": false,
           "rotation": 0
       }')
WHERE NOT EXISTS (
    SELECT 1 FROM namespace_store
    WHERE namespace = 'webcams' AND json_extract(value, '$.name') = '4gx_screen');

COMMIT;
