# Record editor sizing and menu overflow

- More actions disables Arco's default popup height limit. All six actions are visible without horizontal or vertical scrolling.
- Record windows have a bottom-right resize handle supporting pointer dragging and arrow keys (Shift for larger steps). Dimensions remain within the viewport, persist while editing, and leave the save/cancel footer visible.
- Record labels and controls use 12px text, reduced from 14px; auxiliary hints use 10px.
- JSON, Text and other multiline record fields use native two-dimensional resizing instead of automatic height. Manual sizes survive subsequent typing. Other CellValueInput consumers keep automatic sizing.

Verification: build/typecheck and lint passed; 21 value/toolbar unit tests and 7 focused production browser tests passed. Tests cover menu overflow at five widths (1920 through 1024), keyboard/pointer window resizing, both JSON and Text native resize grips, retained dimensions/drafts after input, visible save controls, typed saving, validation and failure recovery. Real Chrome resized the record window from 860×626 to 1060×546, Text from 140px to 220px and JSON from 140px to 260px, then saved successfully. Layout interactions used in-memory fixtures and did not connect to a business database.

Deployment uses the existing application image with only the verified frontend build replaced. Configuration, encryption keys, application databases, cached assets, proxy settings and other services remain in place; a stopped previous container and complete data backup are retained for rollback.
