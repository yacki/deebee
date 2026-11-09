export type CellColumn = { name: string; type?: string; data_type?: string; nullable?: boolean; extra?: string };
export type GridEdit = { rowIndex: number; changes: Record<string, unknown> };
export function columnType(column?: CellColumn) { return (column?.data_type || column?.type || '').toLowerCase(); }
export function isGenerated(column: CellColumn) { return /(?:virtual|stored)\s+generated|generated\s+always|identity|auto_increment/i.test(column.extra || ''); }
export function isBinary(column?: CellColumn) { return /^(?:(?:tiny|medium|long)?blob|(?:var)?binary|bytea)/.test(columnType(column)); }
export function enumOptions(column?: CellColumn) {
  const type = columnType(column);
  if (!type.startsWith('enum(')) return [];
  return Array.from((column?.data_type || column?.type || '').matchAll(/'((?:[^'\\]|\\.|'')*)'/g), match => match[1].replace(/''/g, "'").replace(/\\(.)/g, '$1'));
}
export function cellText(value: unknown, column?: CellColumn) {
  if (/^bit/.test(columnType(column)) && value && typeof value==='object' && '$binary' in value) return BigInt('0x'+((value as {$binary:string}).$binary||'00')).toString();
  if (value === null || value === undefined) return '';
  if (typeof value === 'object' && '$binary' in value && typeof (value as {$binary:unknown}).$binary === 'string') return '0x' + (value as {$binary:string}).$binary;
  return typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value);
}
export function parseCellValue(text: string, column: CellColumn, nullValue = false): unknown {
  if (nullValue) {
    if (column.nullable === false) throw new Error(`${column.name} 不允许 NULL`);
    return null;
  }
  const type = columnType(column), trimmed = text.trim();
  if (/^json/.test(type)) { try { JSON.parse(text); } catch { throw new Error(`${column.name} 的 JSON 格式无效`); } return text; }
  if (isBinary(column)) {
    const hex = trimmed.replace(/^0x/i, '');
    if (!/^(?:[\da-f]{2})*$/i.test(hex)) throw new Error(`${column.name} 需要偶数位十六进制数据，例如 0x00FF`);
    return { $binary: hex.toLowerCase(), size: hex.length / 2 };
  }
  if (/^(tinyint|smallint|mediumint|int|integer|bigint|bit|year|bool)/.test(type)) {
    const normalized = /^bit/.test(type) && /^0x[\da-f]+$/i.test(trimmed) ? BigInt(trimmed).toString() : /^true$/i.test(trimmed) ? '1' : /^false$/i.test(trimmed) ? '0' : trimmed;
    if (!/^[+-]?\d+$/.test(normalized)) throw new Error(`${column.name} 必须是整数`);
    const value = BigInt(normalized);
    return value <= BigInt(Number.MAX_SAFE_INTEGER) && value >= BigInt(Number.MIN_SAFE_INTEGER) ? Number(value) : value.toString();
  }
  if (/^(decimal|numeric|float|double|real)/.test(type)) {
    if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(trimmed) || !Number.isFinite(Number(trimmed))) throw new Error(`${column.name} 必须是有效数字`);
    return /^(decimal|numeric)/.test(type) ? trimmed : Number(trimmed);
  }
  return text;
}
export function delimitedText(rows: unknown[][], delimiter = '\t') {
  return rows.map(row => row.map(value => {
    const raw=cellText(value);
    const text = value === null ? '\\N' : typeof value==='string' && (raw==='\\N'||raw.startsWith('\\\\')) ? '\\'+raw : raw;
    return text==='' || text.includes(delimiter) || /["\r\n]/.test(text) ? '"' + text.replaceAll('"', '""') + '"' : text;
  }).join(delimiter)).join('\n');
}
export function parseDelimited(text: string, delimiter = '\t') {
  const rows: string[][] = []; let row: string[] = [], field = '', quoted = false;
  for (let i=0;i<text.length;i++) {
    const c=text[i];
    if (c==='"' && (quoted || !field)) { if (quoted && text[i+1]==='"') {field+='"';i++;} else quoted=!quoted; }
    else if (!quoted && c===delimiter) {row.push(field);field='';}
    else if (!quoted && (c==='\n'||c==='\r')) {if(c==='\r'&&text[i+1]==='\n')i++;row.push(field);rows.push(row);row=[];field='';}
    else field+=c;
  }
  if (quoted) throw new Error('粘贴内容包含未闭合的引号');
  if (field || row.length || (text.length && !/[\r\n]$/.test(text))) {row.push(field);rows.push(row);}
  return rows;
}

export function parsePastedValue(text: string, column: CellColumn) {
  return text==='\\N' ? parseCellValue('',column,true) : parseCellValue(text.startsWith('\\\\') ? text.slice(1) : text,column);
}

export function formatCellJson(text: string) {
  JSON.parse(text);
  const tokens=text.match(/"(?:\\.|[^"\\])*"|[^\s{}[\],:]+|[{}[\],:]/g)||[];
  let output='',depth=0;
  const newline=()=>{output+='\n'+'  '.repeat(depth);};
  tokens.forEach((token,index)=>{
    if(token==='{'||token==='['){output+=token;depth++;if(tokens[index+1]!=='}'&&tokens[index+1]!==']')newline();}
    else if(token==='}'||token===']'){depth--;if(tokens[index-1]!=='{'&&tokens[index-1]!=='[')newline();output+=token;}
    else if(token===','){output+=token;newline();}
    else if(token===':')output+=': ';
    else output+=token;
  });
  return output;
}
