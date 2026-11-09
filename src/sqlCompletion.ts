import { completionContext } from "./sqlAnalysis";
import type { Catalog, CatalogTable } from "./types";

type Dialect = "mysql" | "postgresql" | "mssql";
export type SqlSuggestion = {
  label: string;
  kind: "keyword" | "table" | "view" | "column" | "function";
  detail: string;
  insertText: string;
  filterText: string;
  sortText: string;
  snippet?: boolean;
};

const commonFunctions = ["COUNT", "SUM", "AVG", "MIN", "MAX", "CONCAT", "COALESCE", "NULLIF", "UPPER", "LOWER", "TRIM", "ROUND", "CAST"];
const functions: Record<Dialect, string[]> = {
  mysql: [...commonFunctions, "NOW", "DATE_FORMAT", "IFNULL", "JSON_EXTRACT"],
  postgresql: [...commonFunctions, "NOW", "TO_CHAR", "JSONB_BUILD_OBJECT", "ARRAY_AGG", "STRING_AGG"],
  mssql: [...commonFunctions, "GETDATE", "ISNULL", "TRY_CONVERT", "TRY_CAST", "SCOPE_IDENTITY", "STRING_AGG"],
};

export function sqlCompletions(sql: string, offset: number, catalog?: Catalog, dialect: Dialect = "mysql") {
  const context = completionContext(sql, offset);
  const suggestions: SqlSuggestion[] = [];
  const seen = new Set<string>();
  const prefix = context.prefix.toLowerCase();
  const add = (label: string, kind: SqlSuggestion["kind"], detail: string, order: number, insertText = label, filterText = label, snippet = false) => {
    // Monaco's fuzzy matcher can match 'whe' to 'dev_webhook'. Keep SQL
    // completion deterministic and restrict candidates to the typed prefix.
    if (!filterText.toLowerCase().startsWith(prefix) || seen.has(label.toLowerCase())) return;
    seen.add(label.toLowerCase());
    suggestions.push({ label, kind, detail, insertText, filterText, sortText: `${order}_${label.toLowerCase()}`, snippet });
  };
  const addKeywords = (values: string[], order = 0) => {
    if (!context.quoted) for (const value of values) add(value, "keyword", "关键字", order);
  };
  const addTables = () => {
    for (const table of catalog?.tables ?? []) add(table.name, table.object_type === "VIEW" ? "view" : "table", `${table.object_type === "VIEW" ? "视图" : "表"} · ${table.columns.length} 列`, 1);
  };
  const matchesNamespace = (name?: string) => !name || [catalog?.database, catalog?.schema].some(value => value?.toLowerCase() === name.toLowerCase());
  const findTable = (name: string, namespace?: string) => matchesNamespace(namespace) ? catalog?.tables.find(table => table.name.toLowerCase() === name.toLowerCase()) : undefined;
  const addColumns = (sources: { table: CatalogTable; alias?: string }[], qualified = false) => {
    const counts = new Map<string, number>();
    for (const source of sources) for (const column of source.table.columns) counts.set(column.name.toLowerCase(), (counts.get(column.name.toLowerCase()) ?? 0) + 1);
    for (const { table, alias } of sources) for (const column of table.columns) {
      const label = !qualified && counts.get(column.name.toLowerCase())! > 1 ? `${alias || table.name}.${column.name}` : column.name;
      add(label, "column", `${column.data_type} · ${table.name}`, 0, label, qualified ? column.name : prefix && label.toLowerCase().startsWith(prefix) ? label : column.name);
    }
  };
  if (context.position === "none") return { context, suggestions };
  if (context.qualifier) {
    if (context.tableContext) {
      if (matchesNamespace(context.qualifier)) addTables();
    } else {
      const source = context.references.find(reference => (reference.alias || reference.table).toLowerCase() === context.qualifier!.toLowerCase());
      const table = source && findTable(source.table, source.qualifier);
      if (table) addColumns([{ table }], true);
    }
    return { context, suggestions };
  }
  if (context.tableContext) { addTables(); return { context, suggestions }; }

  const continuations: Record<string, string[]> = {
    ORDER: ["BY"], GROUP: ["BY"], LEFT: ["JOIN", "OUTER JOIN"], RIGHT: ["JOIN", "OUTER JOIN"],
    INNER: ["JOIN"], FULL: ["JOIN", "OUTER JOIN"], CROSS: [dialect === "mssql" ? "APPLY" : "JOIN"],
    INSERT: ["INTO"], DELETE: ["FROM"], IS: ["NULL", "NOT NULL"], NOT: ["IN", "LIKE", "BETWEEN", "EXISTS", "NULL"],
    UNION: ["ALL", "SELECT"],
  };
  if (continuations[context.previous]) {
    addKeywords(continuations[context.previous]);
    return { context, suggestions };
  }
  if (context.position === "statement") {
    addKeywords(["SELECT", "INSERT INTO", "UPDATE", "DELETE FROM", "WITH", "CREATE TABLE", "ALTER TABLE", "DROP TABLE", "EXPLAIN"]);
    return { context, suggestions };
  }
  const pagination = dialect === "mssql" ? ["OFFSET", "FETCH NEXT"] : ["LIMIT", "OFFSET"];
  const clauses = ["WHERE", "JOIN", "LEFT JOIN", "RIGHT JOIN", "INNER JOIN", "GROUP BY", "ORDER BY", ...pagination];
  if (context.position === "clause") {
    addKeywords(context.clause === "UPDATE" ? ["SET"] : context.clause === "INTO" ? ["VALUES", "SELECT"] : context.clause === "TABLE" ? ["ADD", "ALTER", "DROP"] : context.clause === "JOIN" ? ["ON", "USING", ...clauses] : clauses);
    return { context, suggestions };
  }

  const sources = context.references.flatMap(reference => {
    const table = findTable(reference.table, reference.qualifier);
    return table ? [{ table, alias: reference.alias }] : [];
  });
  const operand = ["SELECT", "WHERE", "ON", "SET", "HAVING", "RETURNING", "BY", "AND", "OR", "NOT", "WHEN", "THEN", "ELSE", "(", ",", "=", "<", ">", "!", "+", "-", "*", "/", "LIKE", "ILIKE", "BETWEEN", "IN"].includes(context.previous);
  if (operand || context.qualifier || context.quoted) addColumns(sources);
  if (context.clause === "SELECT") addKeywords(["DISTINCT", "CASE", "WHEN", "THEN", "ELSE", "END", "AS", "FROM", "NULL", ...(dialect === "mssql" ? ["TOP"] : [])], 1);
  else if (["ORDER", "GROUP"].includes(context.clause)) addKeywords(operand ? [] : ["ASC", "DESC", "HAVING", "ORDER BY", ...pagination]);
  else if (["LIMIT", "OFFSET", "FETCH"].includes(context.clause)) addKeywords(["OFFSET", "FETCH NEXT", "ROWS", "ONLY"]);
  else addKeywords(operand ? ["NOT", "NULL", "EXISTS", "CASE"] : ["AND", "OR", "IS NULL", "IS NOT NULL", "IN", "NOT IN", "LIKE", "BETWEEN", "GROUP BY", "ORDER BY", ...pagination, ...(dialect === "postgresql" ? ["ILIKE"] : [])], 1);
  // Functions are useful when explicitly started; do not flood an empty WHERE
  // slot with every built-in and every routine in the database.
  if (!context.quoted && operand && (prefix || context.clause === "SELECT")) {
    for (const name of functions[dialect]) add(name, "function", "函数", 2, `${name}($0)`, name, true);
    for (const routine of prefix ? catalog?.routines ?? [] : []) {
      if (routine.object_type.toUpperCase() === "FUNCTION") add(routine.name, "function", `函数 · ${routine.data_type}`, 2, `${routine.name}($0)`, routine.name, true);
    }
  }
  suggestions.sort((a, b) => a.sortText.localeCompare(b.sortText));
  return { context, suggestions };
}
