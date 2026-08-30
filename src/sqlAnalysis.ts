export type SqlTableReference = {
  table: string;
  qualifier?: string;
  alias?: string;
};

export type SqlCompletionContext = {
  references: SqlTableReference[];
  qualifier?: string;
  tableContext: boolean;
};

type TokenKind = "word" | "identifier" | "string" | "number" | "symbol";
type Token = { value: string; upper: string; kind: TokenKind; start: number; end: number; depth: number };

const RESERVED = new Set([
  "ALL", "ALTER", "AND", "AS", "ASC", "BETWEEN", "BY", "CASE", "CHECK", "CROSS", "DELETE", "DESC",
  "DISTINCT", "ELSE", "END", "EXCEPT", "EXISTS", "FETCH", "FOR", "FROM", "FULL", "GROUP", "HAVING",
  "ILIKE", "IN", "INNER", "INSERT", "INTERSECT", "INTO", "IS", "JOIN", "LATERAL", "LEFT", "LIKE", "LIMIT",
  "LOCK", "NATURAL", "NOT", "NULL", "OFFSET", "ON", "OR", "ORDER", "OUTER", "RETURNING", "RIGHT", "SET",
  "TABLE", "THEN", "UNION", "UPDATE", "USE", "USING", "VALUES", "WHEN", "WHERE", "WINDOW", "WITH",
]);
const SOURCE_KEYWORDS = new Set(["FROM", "JOIN", "UPDATE", "INTO", "TABLE"]);
const CLAUSE_KEYWORDS = new Set([
  "WHERE", "ON", "GROUP", "ORDER", "HAVING", "LIMIT", "OFFSET", "UNION", "INTERSECT", "EXCEPT", "RETURNING",
  "SET", "VALUES", "WINDOW", "FETCH", "FOR",
]);

function isWordStart(char: string) { return /[A-Za-z_$\u0080-\uFFFF]/.test(char); }
function isWordPart(char: string) { return /[A-Za-z0-9_$\u0080-\uFFFF]/.test(char); }

function tokenize(sql: string): { tokens: Token[]; cursorDepth: number } {
  const tokens: Token[] = [];
  let index = 0;
  let depth = 0;
  while (index < sql.length) {
    const char = sql[index];
    const next = sql[index + 1];
    if (/\s/.test(char)) { index += 1; continue; }
    if ((char === "-" && next === "-") || char === "#") {
      index += char === "#" ? 1 : 2;
      while (index < sql.length && sql[index] !== "\n") index += 1;
      continue;
    }
    if (char === "/" && next === "*") {
      const end = sql.indexOf("*/", index + 2);
      index = end < 0 ? sql.length : end + 2;
      continue;
    }
    if (char === "'") {
      const start = index++;
      while (index < sql.length) {
        if (sql[index] === "\\") { index += 2; continue; }
        if (sql[index] === "'" && sql[index + 1] === "'") { index += 2; continue; }
        if (sql[index++] === "'") break;
      }
      tokens.push({ value: sql.slice(start, index), upper: "", kind: "string", start, end: index, depth });
      continue;
    }
    if (char === "$" ) {
      const marker = sql.slice(index).match(/^\$(?:[A-Za-z_][\w$]*)?\$/)?.[0];
      if (marker) {
        const start = index; const end = sql.indexOf(marker, index + marker.length);
        index = end < 0 ? sql.length : end + marker.length;
        tokens.push({ value: sql.slice(start, index), upper: "", kind: "string", start, end: index, depth });
        continue;
      }
    }
    if (char === "`" || char === '"') {
      const quote = char; const start = index++; let value = "";
      while (index < sql.length) {
        if (sql[index] === quote && sql[index + 1] === quote) { value += quote; index += 2; continue; }
        if (sql[index] === quote) { index += 1; break; }
        value += sql[index++];
      }
      tokens.push({ value, upper: value.toUpperCase(), kind: "identifier", start, end: index, depth });
      continue;
    }
    if (isWordStart(char)) {
      const start = index++;
      while (index < sql.length && isWordPart(sql[index])) index += 1;
      const value = sql.slice(start, index);
      tokens.push({ value, upper: value.toUpperCase(), kind: "word", start, end: index, depth });
      continue;
    }
    if (/[0-9]/.test(char)) {
      const start = index++;
      while (index < sql.length && /[0-9.eE+-]/.test(sql[index])) index += 1;
      const value = sql.slice(start, index);
      tokens.push({ value, upper: value.toUpperCase(), kind: "number", start, end: index, depth });
      continue;
    }
    if (char === ")") depth = Math.max(0, depth - 1);
    tokens.push({ value: char, upper: char, kind: "symbol", start: index, end: index + 1, depth });
    if (char === "(") depth += 1;
    index += 1;
  }
  return { tokens, cursorDepth: depth };
}

function identifier(token?: Token) {
  return token && (token.kind === "word" || token.kind === "identifier") && !RESERVED.has(token.upper);
}

function readReference(tokens: Token[], start: number, depth: number): { reference?: SqlTableReference; next: number; derived: boolean } {
  if (tokens[start]?.value === "(") return { next: start + 1, derived: true };
  if (!identifier(tokens[start]) || tokens[start].depth !== depth) return { next: start, derived: false };
  const parts = [tokens[start].value];
  let index = start + 1;
  while (tokens[index]?.value === "." && tokens[index].depth === depth && identifier(tokens[index + 1]) && tokens[index + 1].depth === depth) {
    parts.push(tokens[index + 1].value);
    index += 2;
  }
  // A name followed by '(' is a table-valued function, not a base table.
  if (tokens[index]?.value === "(" && tokens[index].depth === depth) return { next: index + 1, derived: true };
  let alias: string | undefined;
  if (tokens[index]?.upper === "AS" && identifier(tokens[index + 1])) { alias = tokens[index + 1].value; index += 2; }
  else if (identifier(tokens[index])) { alias = tokens[index].value; index += 1; }
  return {
    reference: { table: parts.at(-1)!, qualifier: parts.length > 1 ? parts.at(-2) : undefined, alias },
    next: index,
    derived: false,
  };
}

function referencesAtDepth(tokens: Token[], depth: number) {
  const references: SqlTableReference[] = [];
  let derived = false;
  let joined = false;
  let commaSource = false;
  let inFromList = false;
  let expectCommaSource = false;
  for (let index = 0; index < tokens.length; index += 1) {
    const token = tokens[index];
    if (token.depth !== depth) continue;
    if (CLAUSE_KEYWORDS.has(token.upper)) { inFromList = false; expectCommaSource = false; continue; }
    if (token.upper === "JOIN") { joined = true; inFromList = false; expectCommaSource = false; }
    if (token.upper === "FROM") { inFromList = true; expectCommaSource = true; continue; }
    if (token.upper === "JOIN" || token.upper === "UPDATE" || token.upper === "INTO") {
      const parsed = readReference(tokens, index + 1, depth);
      if (parsed.reference) references.push(parsed.reference);
      derived ||= parsed.derived;
      index = Math.max(index, parsed.next - 1);
      continue;
    }
    if (inFromList && token.value === ",") { commaSource = true; expectCommaSource = true; continue; }
    if (inFromList && expectCommaSource) {
      const parsed = readReference(tokens, index, depth);
      if (parsed.reference) references.push(parsed.reference);
      derived ||= parsed.derived;
      expectCommaSource = false;
      index = Math.max(index, parsed.next - 1);
    }
  }
  return { references, derived, joined, commaSource };
}

export function currentSqlBeforeCursor(sql: string, offset: number) {
  const before = sql.slice(0, offset);
  const { tokens } = tokenize(before);
  let start = 0;
  for (const token of tokens) if (token.value === ";" && token.depth === 0) start = token.end;
  return before.slice(start);
}

export function completionContext(sqlBeforeCursor: string): SqlCompletionContext {
  const { tokens, cursorDepth } = tokenize(sqlBeforeCursor);
  const sameDepth = tokens.filter(token => token.depth === cursorDepth);
  const { references } = referencesAtDepth(tokens, cursorDepth);
  let qualifier: string | undefined;
  const last = sameDepth.at(-1);
  const previous = sameDepth.at(-2);
  const third = sameDepth.at(-3);
  if (last?.value === "." && identifier(previous)) qualifier = previous!.value;
  else if (identifier(last) && previous?.value === "." && identifier(third)) qualifier = third!.value;

  let boundary = -1;
  for (let index = sameDepth.length - 1; index >= 0; index -= 1) {
    const token = sameDepth[index];
    if (SOURCE_KEYWORDS.has(token.upper) || CLAUSE_KEYWORDS.has(token.upper) || token.value === "," || token.upper === "SELECT") { boundary = index; break; }
  }
  const boundaryToken = sameDepth[boundary];
  const tail = sameDepth.slice(boundary + 1);
  const tableContext = Boolean(boundaryToken && (SOURCE_KEYWORDS.has(boundaryToken.upper) || boundaryToken.value === ",") && tail.every(token => identifier(token) || token.value === "."));
  return { references, qualifier, tableContext };
}

export function splitSqlStatements(sql: string) {
  const { tokens } = tokenize(sql);
  const statements: string[] = [];
  let start = 0;
  for (const token of tokens) {
    if (token.value !== ";" || token.depth !== 0) continue;
    const statement = sql.slice(start, token.start).trim();
    if (statement) statements.push(statement);
    start = token.end;
  }
  const tail = sql.slice(start).trim();
  if (tail) statements.push(tail);
  return statements;
}

export function inferSingleSelectSource(sql: string): SqlTableReference | undefined {
  const { tokens } = tokenize(sql);
  const root = tokens.filter(token => token.depth === 0);
  if (root[0]?.upper !== "SELECT") return;
  if (root.some(token => ["UNION", "INTERSECT", "EXCEPT"].includes(token.upper))) return;
  const sourceInfo = referencesAtDepth(tokens, 0);
  if (sourceInfo.derived || sourceInfo.joined || sourceInfo.commaSource || sourceInfo.references.length !== 1) return;
  return sourceInfo.references[0];
}

export function inferEditableSingleSelectSource(sql: string): SqlTableReference | undefined {
  const source = inferSingleSelectSource(sql);
  if (!source) return;
  const { tokens } = tokenize(sql);
  const root = tokens.filter(token => token.depth === 0);
  if (root.some(token => ["DISTINCT", "GROUP", "HAVING", "WINDOW"].includes(token.upper))) return;
  const from = root.findIndex(token => token.upper === "FROM");
  if (from < 2) return;
  const projection = root.slice(1, from);
  const expressions: Token[][] = [[]];
  for (const token of projection) {
    if (token.value === ",") expressions.push([]);
    else expressions.at(-1)!.push(token);
  }
  if (expressions.some(expression => !expression.length)) return;
  const sourceNames = new Set([source.table, source.alias].filter(Boolean).map(name => name!.toLowerCase()));
  for (const expression of expressions) {
    if (expression.length === 1 && (expression[0].value === "*" || identifier(expression[0]))) continue;
    if (expression.length < 3 || expression.length % 2 === 0) return;
    for (let index = 0; index < expression.length; index += 1) {
      if (index % 2 === 1) { if (expression[index].value !== ".") return; }
      else if (index === expression.length - 1) { if (expression[index].value !== "*" && !identifier(expression[index])) return; }
      else if (!identifier(expression[index])) return;
    }
    const tableQualifier = expression.at(-3)!.value.toLowerCase();
    if (!sourceNames.has(tableQualifier)) return;
  }
  return source;
}
