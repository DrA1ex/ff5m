BEGIN TRANSACTION;

WITH cte AS (SELECT '$.stored' AS path)
UPDATE namespace_store
SET value = json_insert(value, printf("%s[%i]", (SELECT path FROM cte), json_array_length(json_extract(value, (SELECT path FROM cte)))),
    json('{
        "alias": "RESTORE PRINT",
        "categoryId": "85e2f576-74e1-4bd3-9e82-fca937d1d3ce",
        "color": "",
        "disabledWhilePrinting": true,
        "name": "resurrect",
        "visible": true
    }')
)
WHERE namespace = 'fluidd' AND key = 'macros';


WITH cte AS (SELECT '$.macrogroups.b030cdb5-37a7-4280-b3d1-e2cbd1cdcdc0.macros' AS path)
UPDATE namespace_store
SET value = json_insert(value, printf("%s[%i]", (SELECT path FROM cte), json_array_length(json_extract(value, (SELECT path FROM cte)))),
    json('{
        "color": "group",
        "name": "RESURRECT",
        "pos": 10,
        "showInPause": false,
        "showInPrinting": false,
        "showInStandby": true
    }')
)
WHERE namespace = 'mainsail' AND key = 'macros';

COMMIT;
