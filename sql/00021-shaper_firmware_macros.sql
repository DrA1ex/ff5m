-- Replace legacy shaper controls and add Firmware / Recovery to the web UIs.
-- Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
-- This file may be distributed under the terms of the GNU GPLv3 license.

BEGIN TRANSACTION;

-- Keep Fluidd's hidden entry so the macro does not reappear as uncategorized.
UPDATE namespace_store
SET value = COALESCE(json_set(value, (
    SELECT printf('$.stored[%i].visible', macro.key)
    FROM json_each(namespace_store.value, '$.stored') AS macro
    WHERE lower(json_extract(macro.value, '$.name')) = 'zshaper'
), json('false')), value)
WHERE namespace = 'fluidd' AND key = 'macros';

WITH cte AS (SELECT '$.macrogroups.7ac31722-85f9-4771-9b7c-01597bc176fd.macros' AS path)
UPDATE namespace_store
SET value = COALESCE(json_remove(value, (
    SELECT printf("%s[%i]", (SELECT path FROM cte), macro.key)
    FROM json_each(json_extract(namespace_store.value, (SELECT path FROM cte))) AS macro
    WHERE upper(json_extract(macro.value, '$.name')) = 'ZSHAPER'
)), value)
WHERE namespace = 'mainsail' AND key = 'macros';

-- Also hide the command in Mainsail's ungrouped view.
UPDATE namespace_store
SET value = json_set(value, '$.hiddenMacros', json_insert(
    COALESCE(json_extract(value, '$.hiddenMacros'), json('[]')),
    printf('$[%i]', COALESCE(json_array_length(json_extract(value, '$.hiddenMacros')), 0)),
    'ZSHAPER'))
WHERE namespace = 'mainsail' AND key = 'macros'
  AND NOT EXISTS (
      SELECT 1 FROM json_each(namespace_store.value, '$.hiddenMacros')
      WHERE upper(json_each.value) = 'ZSHAPER');

-- A previous run without -bail may have committed these additions after an error.
-- Fluidd resolves category settings by the first matching ID; keep that entry.
UPDATE namespace_store
SET value = json_set(value, '$.categories', json((
    SELECT json_group_array(json(item)) FROM (
        SELECT category.value AS item
        FROM json_each(namespace_store.value, '$.categories') AS category
        WHERE COALESCE(json_extract(category.value, '$.id'), '') != '3a3ac592-898a-4eb5-b02d-9eda04e4488a'
           OR category.key = (
               SELECT MIN(duplicate.key)
               FROM json_each(namespace_store.value, '$.categories') AS duplicate
               WHERE json_extract(duplicate.value, '$.id') = '3a3ac592-898a-4eb5-b02d-9eda04e4488a')
        ORDER BY category.key
    )
)))
WHERE namespace = 'fluidd' AND key = 'macros'
  AND (
      SELECT COUNT(*) FROM json_each(namespace_store.value, '$.categories') AS category
      WHERE json_extract(category.value, '$.id') = '3a3ac592-898a-4eb5-b02d-9eda04e4488a') > 1;

WITH cte AS (SELECT '$.categories' AS path)
UPDATE namespace_store
SET value = json_insert(
    value,
    printf("%s[%i]", (SELECT path FROM cte),
           json_array_length(json_extract(value, (SELECT path FROM cte)))),
    json('{
        "id": "3a3ac592-898a-4eb5-b02d-9eda04e4488a",
        "name": "Firmware / Recovery"
    }')
)
WHERE namespace = 'fluidd' AND key = 'macros'
  AND NOT EXISTS (
      SELECT 1 FROM json_each(namespace_store.value, '$.categories') AS category
      WHERE json_extract(category.value, '$.id') = '3a3ac592-898a-4eb5-b02d-9eda04e4488a');

WITH cte AS (SELECT '$.stored' AS path)
UPDATE namespace_store
SET value = json_insert(
    value,
    printf("%s[%i]", (SELECT path FROM cte),
           json_array_length(json_extract(value, (SELECT path FROM cte)))),
    json('{
        "name": "forge_shaper",
        "alias": "Input Shaper",
        "categoryId": "244d667b-c410-4e01-9bf1-e8e0b9deabe2",
        "order": 8,
        "color": "",
        "visible": true,
        "disabledWhilePrinting": true
    }')
)
WHERE namespace = 'fluidd' AND key = 'macros'
  AND NOT EXISTS (
      SELECT 1 FROM json_each(namespace_store.value, '$.stored') AS macro
      WHERE lower(json_extract(macro.value, '$.name')) = 'forge_shaper');

WITH cte AS (SELECT '$.stored' AS path)
UPDATE namespace_store
SET value = json_insert(
    value,
    printf("%s[%i]", (SELECT path FROM cte),
           json_array_length(json_extract(value, (SELECT path FROM cte)))),
    json('{
        "name": "download_firmware_update",
        "alias": "Download firmware",
        "categoryId": "3a3ac592-898a-4eb5-b02d-9eda04e4488a",
        "order": 1,
        "color": "",
        "visible": true,
        "disabledWhilePrinting": true
    }')
)
WHERE namespace = 'fluidd' AND key = 'macros'
  AND NOT EXISTS (
      SELECT 1 FROM json_each(namespace_store.value, '$.stored') AS macro
      WHERE lower(json_extract(macro.value, '$.name')) = 'download_firmware_update');

WITH cte AS (SELECT '$.stored' AS path)
UPDATE namespace_store
SET value = json_insert(
    value,
    printf("%s[%i]", (SELECT path FROM cte),
           json_array_length(json_extract(value, (SELECT path FROM cte)))),
    json('{
        "name": "flash_firmware",
        "alias": "Flash firmware",
        "categoryId": "3a3ac592-898a-4eb5-b02d-9eda04e4488a",
        "order": 2,
        "color": "",
        "visible": true,
        "disabledWhilePrinting": true
    }')
)
WHERE namespace = 'fluidd' AND key = 'macros'
  AND NOT EXISTS (
      SELECT 1 FROM json_each(namespace_store.value, '$.stored') AS macro
      WHERE lower(json_extract(macro.value, '$.name')) = 'flash_firmware');

WITH cte AS (SELECT '$.stored' AS path)
UPDATE namespace_store
SET value = json_insert(
    value,
    printf("%s[%i]", (SELECT path FROM cte),
           json_array_length(json_extract(value, (SELECT path FROM cte)))),
    json('{
        "name": "enter_recovery",
        "alias": "Enter Recovery",
        "categoryId": "3a3ac592-898a-4eb5-b02d-9eda04e4488a",
        "order": 3,
        "color": "",
        "visible": true,
        "disabledWhilePrinting": true
    }')
)
WHERE namespace = 'fluidd' AND key = 'macros'
  AND NOT EXISTS (
      SELECT 1 FROM json_each(namespace_store.value, '$.stored') AS macro
      WHERE lower(json_extract(macro.value, '$.name')) = 'enter_recovery');

WITH cte AS (SELECT '$.stored' AS path)
UPDATE namespace_store
SET value = json_insert(
    value,
    printf("%s[%i]", (SELECT path FROM cte),
           json_array_length(json_extract(value, (SELECT path FROM cte)))),
    json('{
        "name": "prepare_usb",
        "alias": "Prepare USB",
        "categoryId": "3a3ac592-898a-4eb5-b02d-9eda04e4488a",
        "order": 4,
        "color": "",
        "visible": true,
        "disabledWhilePrinting": true
    }')
)
WHERE namespace = 'fluidd' AND key = 'macros'
  AND NOT EXISTS (
      SELECT 1 FROM json_each(namespace_store.value, '$.stored') AS macro
      WHERE lower(json_extract(macro.value, '$.name')) = 'prepare_usb');

WITH cte AS (SELECT '$.macrogroups.7ac31722-85f9-4771-9b7c-01597bc176fd.macros' AS path)
UPDATE namespace_store
SET value = json_insert(
    value,
    printf("%s[%i]", (SELECT path FROM cte),
           json_array_length(json_extract(value, (SELECT path FROM cte)))),
    json('{
        "name": "FORGE_SHAPER",
        "pos": 8,
        "color": "group",
        "showInStandby": true,
        "showInPrinting": false,
        "showInPause": false
    }')
)
WHERE namespace = 'mainsail' AND key = 'macros'
  AND NOT EXISTS (
      SELECT 1 FROM json_each(json_extract(namespace_store.value, (SELECT path FROM cte))) AS macro
      WHERE upper(json_extract(macro.value, '$.name')) = 'FORGE_SHAPER');

UPDATE namespace_store
SET value = json_insert(value, '$.macrogroups.3a3ac592-898a-4eb5-b02d-9eda04e4488a', json('{
    "name": "8. Firmware / Recovery",
    "color": "primary",
    "colorCustom": "#fff",
    "showInStandby": true,
    "showInPrinting": false,
    "showInPause": false,
    "macros": [
        {
            "name": "DOWNLOAD_FIRMWARE_UPDATE",
            "pos": 1,
            "color": "group",
            "showInStandby": true,
            "showInPrinting": false,
            "showInPause": false
        },
        {
            "name": "FLASH_FIRMWARE",
            "pos": 2,
            "color": "group",
            "showInStandby": true,
            "showInPrinting": false,
            "showInPause": false
        },
        {
            "name": "ENTER_RECOVERY",
            "pos": 3,
            "color": "group",
            "showInStandby": true,
            "showInPrinting": false,
            "showInPause": false
        },
        {
            "name": "PREPARE_USB",
            "pos": 4,
            "color": "group",
            "showInStandby": true,
            "showInPrinting": false,
            "showInPause": false
        }
    ]
}'))
WHERE namespace = 'mainsail' AND key = 'macros';

COMMIT;
