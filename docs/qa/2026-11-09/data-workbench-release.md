# Data workbench release — 2026-11-09

Test environment: https://deebee.idmesh.site/deebee/

## Behavior

- Renaming a connection preserves its sessions. Closing an expired MySQL session tolerates driver errors, so saving a display name does not fail with HTTP 500.
- Grid headers and cells share native scrolling. Data can be viewed as a grid, record, text, or JSON, with inline and popup editing, rectangular/multiple-row copy, and paste preview with transactional batch updates.
- Transfers support CSV and SQL import/export, XLSX export/import, field selection and mapping, CSV encoding/delimiter/NULL settings, query/selection export, and SQL structure-plus-data dumps.
- Typed values preserve large integers, exact decimals, temporal precision, JSON, binary values, and the distinction between SQL NULL, empty text, and literal NULL markers. Generated columns remain read-only.

## Validation

All database writes used newly created `qa_wb_20261109_86f42e_` fixture tables in the explicitly authorized `deebee_e2e` database. Existing tables and other databases were not modified. Fixtures remain available for review.

Chrome verification saved and refreshed 26 editable fields, covering character/text, boolean, integer sizes, unsigned BIGINT, DECIMAL, FLOAT/DOUBLE, DATE, negative extended TIME(6), DATETIME(6), TIMESTAMP(6), YEAR, ENUM, SET, JSON, VARBINARY, BLOB, BIT, and BINARY. A duplicate unique key during multi-row paste rolled back the entire batch.

CSV, XLSX, SQL INSERT, and SQL structure/data round trips each preserved all 4 rows and 28 fields. A GB18030 semicolon CSV also passed manual field mapping. XLSX retained exact numeric strings and formula-looking text as text.

After deployment, public Chrome verification repeated connection rename/save/connect with an intentionally expired test session, inline editing and refresh, popup decimal saving, generated-column protection, copy/paste, all four view modes, CSV import, and XLSX export. Horizontal scroll samples showed zero horizontal offset between matching headers and cells. The temporary connection was removed, and original connection records and secrets were compared for semantic equality.

Checks before release: 39 related backend tests, 19 value/clipboard tests, 7 existing grid regressions, and the isolated frontend build/typecheck passed. These are MySQL live checks; other database drivers were not live-tested for every type.

## Deployment

Application release: `data-workbench-20261109-101928`.

The deployed source was assembled from the previous deployed UI/backend baseline plus the connection/grid/transfer changes. Pending AI smart-run changes were excluded. The preceding commit records the already deployed UI dependencies and query-performance fixes needed to reproduce that baseline.

Only the DeeBee application was recreated. Configuration, keys, data mounts, recordings, old static assets, guacd, and the gateway were preserved. Public HTTPS health, HTML, and entry-asset hashes passed. Temporary verification services, credentials, and the SQL tunnel were removed.

Backups: `/data/deebee/releases/data-workbench-20261109-101928-attempt2/backup`. The previous image and Compose configuration are retained for rollback.

## References

- [DBeaver data viewing and editing](https://dbeaver.com/docs/dbeaver/Data-Viewing-and-Editing/)
- [DBeaver data transfer](https://dbeaver.com/docs/dbeaver/Data-transfer/)
- [DBeaver data export](https://dbeaver.com/docs/dbeaver/Data-export/)
