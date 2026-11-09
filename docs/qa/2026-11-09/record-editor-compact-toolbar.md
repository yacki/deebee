# Record editor and compact toolbar

The popup action now opens a form for the complete selected record. Grid buttons, the context menu and Shift+Enter share this entry point. All columns are visible; primary keys and generated fields remain read-only. Only changed fields are validated and submitted together through the existing row-update API. Cancel and unchanged saves send no update; validation and server failures retain the draft.

The top toolbar keeps connection/database/query/table actions visible and groups views, functions, triggers, events, privileges and backup in More. Query shortcuts use labeled icons, while connection selectors and status text have bounded widths. Every action remains reachable at smaller widths.

Validation:

- Production build and lint passed; 21 value/toolbar unit checks passed.
- All 6 new browser checks passed: typed record saving, JSON validation, retained drafts after server failure, cancel/no-op behavior, read-only keys/generated values, popup bounds, and toolbar fit/action access at 1920, 1536, 1366, 1280 and 1024 pixels.
- Existing production UI suite: 10 passed; the white-select-background assertion also fails on the previous deployed main build and is unchanged by this patch.
- Real Chrome: one record with 28 columns in the dedicated `qa_wb_20261109_aced56_types` table in authorized `deebee_e2e`; 26 editable fields saved together and reread after refresh. Covered Unicode/text, empty string, booleans, integer sizes/unsigned BIGINT, exact DECIMAL, FLOAT/DOUBLE, DATE/TIME(6)/DATETIME(6)/TIMESTAMP(6)/YEAR, ENUM/SET, JSON with an exact large integer, VARBINARY/BLOB/BIT/BINARY. The generated field updated from its expression and remained read-only.
- Real Chrome checked 1920×1080 and 1366×768. The long form scrolls internally with header/footer visible; the toolbar measured clientWidth=scrollWidth=1366 and all buttons remained within its bounds. SQL scope guards rejected access outside the allowed database and writes outside the dedicated fixture prefix.

Deployment retains the existing data directory, settings, secrets and cached assets. The previous container and complete data backup are retained for rollback.
