# Object information drawer layout

The drawer now has one header and one close control. The duplicate body header and numbered definition terms were removed. Property labels and values use bounded grid columns; dates, collation names, long identifiers and multiline comments wrap within the value column. Splitting only the first full-width colon preserves colons inside comments and timestamps. Maintenance/permission text without a property separator remains full-width.

The object summary is compact, and property labels use higher-contrast text. Existing inspector inputs and backend interfaces are unchanged.

Validation: production build/typecheck and lint passed; all 17 object-catalog browser regressions passed, including single-header/close checks and same-row label/value alignment at 1024 and 1920 pixels. Real Chrome verified eight aligned properties, full timestamps and multiline comments without horizontal overflow. Verification used in-memory metadata fixtures and did not write business databases.

Deployment preserves the existing settings, keys and data mount, and retains a full data backup plus the previous stopped container for rollback.
