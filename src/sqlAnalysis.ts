export type SqlTableReference = {
  table: string;
  qualifier?: string;
  alias?: string;
};

export type SqlCompletionContext = {
  references: SqlTableReference[];
  qualifier?: string;
  tableContext: boolean;
  position: "statement" | "table" | "clause" | "expression" | "none";
  clause: string;
  previous: string;
  prefix: string;
  replaceStart: number;
  replaceEnd: number;
  quoted: boolean;
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

function tokenize(sql: string): { tokens: Token[]; cursorDepth: number; suppressed: boolean } {
  const tokens: Token[] = [];
  let index = 0;
  let depth = 0;
  let suppressed = false;
  while (index < sql.length) {
    const char = sql[index];
    const next = sql[index + 1];
    if (/\s/.test(char)) { index += 1; continue; }
    if ((char === "-" && next === "-") || char === "#") {
      index += char === "#" ? 1 : 2;
      while (index < sql.length && sql[index] !== "\n") index += 1;
      suppressed = index === sql.length;
      continue;
    }
    if (char === "/" && next === "*") {
      const end = sql.indexOf("*/", index + 2);
      suppressed = end < 0;
      index = end < 0 ? sql.length : end + 2;
      continue;
    }
    if (char === "'") {
      const start = index++;
      let closed = false;
      while (index < sql.length) {
        if (sql[index] === "\\") { index += 2; continue; }
        if (sql[index] === "'" && sql[index + 1] === "'") { index += 2; continue; }
        if (sql[index++] === "'") { closed = true; break; }
      }
      suppressed = !closed;
      tokens.push({ value: sql.slice(start, index), upper: "", kind: "string", start, end: index, depth });
      continue;
    }
    if (char === "$" ) {
      const marker = sql.slice(index).match(/^\$(?:[A-Za-z_][\w$]*)?\$/)?.[0];
      if (marker) {
        const start = index; const end = sql.indexOf(marker, index + marker.length);
        suppressed = end < 0;
        index = end < 0 ? sql.length : end + marker.length;
        tokens.push({ value: sql.slice(start, index), upper: "", kind: "string", start, end: index, depth });
        continue;
      }
    }
    if (char === "`" || char === '"' || char === "[") {
      const quote = char === "[" ? "]" : char; const start = index++; let value = "";
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
  return { tokens, cursorDepth: depth, suppressed };
}

function identifier(token?: Token) {
  return token && (token.kind === "identifier" || (token.kind === "word" && !RESERVED.has(token.upper)));
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

export function completionContext(sql: string, offset = sql.length): SqlCompletionContext {
  const before = currentSqlBeforeCursor(sql, offset);
  const start = offset - before.length;
  const { tokens: beforeTokens, cursorDepth, suppressed } = tokenize(before);
  const statement = tokenize(sql.slice(start)).tokens;
  const end = statement.find(token => token.value === ";" && token.depth === 0 && token.start >= before.length)?.start ?? sql.length - start;
  const tokens = statement.filter(token => token.start < end);
  const last = beforeTokens.at(-1);
  const editingWord = last?.end === before.length && (last.kind === "word" || last.kind === "identifier");
  const quoted = Boolean(editingWord && last.kind === "identifier");
  const prefix = editingWord ? last.value : "";
  const fullWord = editingWord ? tokens.find(token => token.start === last.start) : undefined;
  const closingQuote = fullWord && fullWord.end > fullWord.start + 1 && ["`", '"', "]"].includes(sql[start + fullWord.end - 1]);
  const replaceStart = editingWord ? start + last.start + (quoted ? 1 : 0) : offset;
  const replaceEnd = fullWord ? start + fullWord.end - (quoted && closingQuote ? 1 : 0) : offset;
  const structural = editingWord ? beforeTokens.slice(0, -1) : beforeTokens;

  // Find the enclosing query, skipping function parentheses and sibling subqueries.
  let scope: Token[] = [];
  let depth = cursorDepth;
  const opens: Token[] = [];
  for (const token of beforeTokens) {
    if (token.value === "(") opens.push(token);
    if (token.value === ")") opens.pop();
  }
  for (; depth >= 0; depth--) {
    const opening = opens.find(token => token.depth === depth - 1);
    const lower = opening?.end ?? 0;
    const upper = tokens.find(token => token.start >= before.length && token.value === ")" && token.depth === depth - 1)?.start ?? end;
    const atDepth = tokens.filter(token => token.depth === depth && token.start >= lower && token.start < upper);
    const previousUnion = atDepth.filter(token => ["UNION", "EXCEPT", "INTERSECT"].includes(token.upper) && token.start < before.length).at(-1)?.end ?? lower;
    const nextUnion = atDepth.find(token => ["UNION", "EXCEPT", "INTERSECT"].includes(token.upper) && token.start >= before.length)?.start ?? upper;
    scope = tokens.filter(token => token.start >= previousUnion && token.start < nextUnion);
    if (scope.some(token => token.depth === depth && ["SELECT", "UPDATE", "INSERT", "DELETE"].includes(token.upper))) break;
  }
  depth = Math.max(0, depth);
  const { references } = referencesAtDepth(scope, depth);
  const scopeStarts = new Set(scope.map(token => token.start));
  const sameDepth = structural.filter(token => token.depth === depth && scopeStarts.has(token.start));
  const tail = structural.at(-1);
  const qualifier = tail?.value === "." && identifier(structural.at(-2)) ? structural.at(-2)!.value : undefined;
  const boundary = sameDepth.findLastIndex(token => SOURCE_KEYWORDS.has(token.upper) || CLAUSE_KEYWORDS.has(token.upper) || token.upper === "SELECT");
  const clause = sameDepth[boundary]?.upper ?? "";
  const clauseTail = sameDepth.slice(boundary + 1);
  const comma = clauseTail.findLastIndex(token => token.value === ",");
  const sourceTail = comma < 0 ? clauseTail : clauseTail.slice(comma + 1);
  // A completed table (and optional alias) ends the table slot. Only an empty
  // slot or a namespace followed by '.' still expects a table name.
  const tableContext = SOURCE_KEYWORDS.has(clause) && (sourceTail.length === 0 ||
    (sourceTail.at(-1)?.value === "." && sourceTail.every(token => identifier(token) || token.value === ".")));
  let position: SqlCompletionContext["position"] = "expression";
  if (suppressed) position = "none";
  else if (tableContext) position = "table";
  else if (!sameDepth.length) position = "statement";
  else if (SOURCE_KEYWORDS.has(clause) && !qualifier) position = "clause";
  return { references, qualifier, tableContext, position, clause, previous: tail?.upper ?? "", prefix, replaceStart, replaceEnd, quoted };
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
