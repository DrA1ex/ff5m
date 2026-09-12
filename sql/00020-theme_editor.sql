-- Add the Theme Editor launcher to the web UI Mod macro groups.
--
-- Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
--
-- This file may be distributed under the terms of the GNU GPLv3 license

BEGIN TRANSACTION;

WITH cte AS (SELECT '$.stored' AS path)
UPDATE namespace_store
SET value = json_insert(
    value,
    printf("%s[%i]", (SELECT path FROM cte),
           json_array_length(json_extract(value, (SELECT path FROM cte)))),
    json('{
        "categoryId": "d83c5e21-865d-43fd-bf2f-2dfda34ff3af",
        "color": "",
        "disabledWhilePrinting": false,
        "name": "theme_editor",
        "order": 2,
        "visible": true
    }')
)
WHERE namespace = 'fluidd' AND key = 'macros';


WITH cte AS (
    SELECT '$.macrogroups.8a862223-f07a-49c2-b0bd-5473b299abff.macros' AS path
)
UPDATE namespace_store
SET value = json_insert(
    value,
    printf("%s[%i]", (SELECT path FROM cte),
           json_array_length(json_extract(value, (SELECT path FROM cte)))),
    json('{
        "color": "group",
        "name": "THEME_EDITOR",
        "pos": 12,
        "showInPause": true,
        "showInPrinting": true,
        "showInStandby": true
    }')
)
WHERE namespace = 'mainsail' AND key = 'macros';

COMMIT;
