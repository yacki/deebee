export type ConnectionDriver = "mysql" | "postgresql";
export type ConnectionDraft = { name: string; host: string; port: number; user: string; password: string; default_database: string; default_schema: string; driver: ConnectionDriver };
export type Profile = Omit<ConnectionDraft, "password"> & { id: string };
export type Database = { name: string; charset: string; collation: string };
export type DatabaseSchema = { name: string; owner?: string; system?: boolean };
export type DbObject = { name: string; object_type: string; object_id?: number; identity_arguments?: string; estimated_rows?: number; comment?: string; engine?: string; data_length?: number; index_length?: number; created_at?: string; updated_at?: string; collation?: string; status?: string; table_name?: string };
export type Objects = { tables: DbObject[]; views: DbObject[]; routines: DbObject[]; triggers: DbObject[]; events: DbObject[] };
export type Column = { name: string; data_type: string; nullable: boolean; default: unknown; extra: string; comment: string; generation?: string; position?: number };
export type Index = { name: string; unique: boolean; type?: string; columns: string[] };
export type ForeignKey = { name: string; columns: string[]; referenced_schema?: string; referenced_table: string; referenced_columns: string[]; on_delete: string; on_update: string };
export type CheckConstraint = { name: string; clause: string };
export type TableSchema = { database: string; schema: string; table: string; columns: Column[]; indexes: Index[]; primary_key: string[]; foreign_keys: ForeignKey[]; checks: CheckConstraint[]; create_sql: string; engine: string; charset: string; collation: string; comment: string };
export type TableData = { columns: Column[]; primary_key: string[]; rows: Record<string, unknown>[]; page: number; page_size: number; total: number };
export type ResultSet = { index: number; kind: "rows" | "mutation"; columns?: { name: string; type: string; nullable: boolean }[]; rows?: Record<string, unknown>[]; row_count?: number; truncated?: boolean; affected_rows?: number; last_insert_id?: number };
export type QueryResponse = { ok: boolean; results: ResultSet[]; elapsed_ms: number; autocommit: boolean; warnings?: Record<string, unknown>[]; status?: Record<string, unknown> };
export type CatalogColumn = { name: string; data_type: string; nullable: boolean; key?: string };
export type CatalogTable = { name: string; object_type: string; columns: CatalogColumn[] };
export type Catalog = { database: string; schema?: string; tables: CatalogTable[]; routines: { name: string; object_type: string; data_type: string }[] };
export type TableSpec = { database: string; schema: string; table: string; columns: Column[]; primary_key: string[]; indexes: Index[]; foreign_keys: ForeignKey[]; checks: CheckConstraint[]; engine: string; charset: string; collation: string; comment: string };

export type QueryTab = { id: string; type: "query"; title: string; database: string; schema: string; sessionId: string; sql: string; autocommit: boolean; loading: boolean; response?: QueryResponse; error?: string; resultIndex: number };
export type DataTab = { id: string; type: "data"; title: string; database: string; schema: string; table: string; loading: boolean; data?: TableData; error?: string; page: number; filterColumn: string; filterValue: string; sort?: { column: string; direction: "asc" | "desc" }; selected: number | null };
export type DesignerTab = { id: string; type: "designer"; title: string; database: string; schema: string; table: string; currentTable: string | null; loading: boolean; spec?: TableSpec; source?: TableSchema; error?: string; pane: "columns" | "indexes" | "foreign" | "checks" | "ddl"; statements: string[]; dangerous: string[] };
export type ObjectsTab = { id: string; type: "objects"; title: string; database: string; schema: string; kind: "tables" | "views" | "routines" | "triggers" | "events" };
export type WorkTab = QueryTab | DataTab | DesignerTab | ObjectsTab;

export type MenuItem = { id: string; label?: string; icon?: string; shortcut?: string; danger?: boolean; disabled?: boolean; separator?: boolean; children?: MenuItem[]; action?: () => void | Promise<void> };
export type MenuState = { x: number; y: number; title: string; items: MenuItem[] };
export type WizardState = { kind: "import" | "export" | "generate" | "dump" | "sql"; database: string; schema: string; table?: string };
