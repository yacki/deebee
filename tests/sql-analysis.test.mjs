import assert from 'node:assert/strict';
import test from 'node:test';
import { completionContext, inferEditableSingleSelectSource, inferSingleSelectSource, splitSqlStatements } from '../src/sqlAnalysis.ts';

const context = marked => completionContext(marked.replace('|', ''), marked.includes('|') ? marked.indexOf('|') : marked.length);
for (const [name, sql, position, prefix] of [
  ['partial WHERE after a table', 'SELECT * FROM core_app whe', 'clause', 'whe'],
  ['partial WHERE after an alias', 'SELECT * FROM core_app a whe', 'clause', 'whe'],
  ['FROM table slot', 'SELECT * FROM co', 'table', 'co'],
  ['JOIN table slot', 'SELECT * FROM core_app JOIN co', 'table', 'co'],
  ['FROM comma', 'SELECT * FROM core_app, co', 'table', 'co'],
  ['SELECT comma', 'SELECT id, app| FROM core_app', 'expression', 'app'],
  ['scoped WHERE field', 'SELECT * FROM core_app WHERE acc', 'expression', 'acc'],
  ['namespace table slot', 'SELECT * FROM idmesh.co', 'table', 'co'],
  ['UPDATE transition', 'UPDATE core_app se', 'clause', 'se'],
  ['statement start', 'sel', 'statement', 'sel'],
  ['comment suppression', 'SELECT -- whe', 'none', ''],
  ['block comment suppression', 'SELECT /* whe', 'none', ''],
  ['literal suppression', "SELECT 'it''s whe", 'none', ''],
  ['dollar literal suppression', 'SELECT $body$whe', 'none', ''],
]) test(name, () => { const c = context(sql); assert.equal(c.position, position); assert.equal(c.prefix, prefix); });

test('references include FROM after the cursor', () => assert.equal(context('SELECT app| FROM core_app a').references[0].table, 'core_app'));
test('completed table aliases do not occupy a new table slot', () => assert.equal(context('SELECT * FROM core_app a ').tableContext, false));
test('alias-dot is preserved independently of a field prefix', () => { const c = context('SELECT * FROM core_app a WHERE a.app'); assert.equal(c.qualifier, 'a'); assert.equal(c.prefix, 'app'); });
test('functions inherit the surrounding query rather than other subqueries', () => assert.deepEqual(context('SELECT COALESCE(app|, NULL) FROM core_app').references.map(r => r.table), ['core_app']));
test('sibling subqueries are excluded from current references', () => assert.deepEqual(context('SELECT (SELECT id FROM core_app) FROM core_user WHERE EXISTS (SELECT * FROM active_apps x WHERE x.app|)').references.map(r => r.table), ['active_apps']));
test('UNION branches and statements stay isolated', () => {
  assert.deepEqual(context('SELECT * FROM core_app UNION SELECT * FROM core_user WHERE user').references.map(r => r.table), ['core_user']);
  assert.deepEqual(context("SELECT ';' FROM core_app; SELECT * FROM core_user WHERE user").references.map(r => r.table), ['core_user']);
});
test('replacement consumes a partial word suffix and preserves quotes', () => {
  const c = context('SELECT * FROM core_app whe|re;'); assert.equal(c.replaceStart, 23); assert.equal(c.replaceEnd, 28);
  for (const [open, close] of [['`', '`'], ['"', '"'], ['[', ']']]) {
    const sql = `SELECT ${open}app|${close} FROM core_app`;
    const quoted = context(sql); assert.equal(quoted.quoted, true); assert.equal(quoted.replaceStart, 8); assert.equal(quoted.replaceEnd, 11);
  }
});
test('comment contents do not create table references', () => assert.deepEqual(context('SELECT * FROM core_app /* FROM core_user; */ whe').references.map(r => r.table), ['core_app']));
test('query result editability still rejects joins, subqueries and expressions', () => {
  assert.equal(inferEditableSingleSelectSource('SELECT id, a.app_name FROM core_app a').table, 'core_app');
  for (const sql of ['SELECT COUNT(*) FROM core_app', 'SELECT id AS other FROM core_app', 'SELECT * FROM core_app JOIN core_user ON 1=1', 'SELECT DISTINCT id FROM core_app', 'SELECT * FROM (SELECT * FROM core_app) a', 'SELECT * FROM core_app, core_user']) assert.equal(inferEditableSingleSelectSource(sql), undefined, sql);
  assert.equal(inferSingleSelectSource('SELECT COUNT(*) FROM core_app').table, 'core_app');
});
test('statement splitting still handles literals, comments and quoted reserved names', () => {
  assert.deepEqual(splitSqlStatements("SELECT ';'; SELECT $$x;y$$; /* ; */ SELECT 1"), ["SELECT ';'", 'SELECT $$x;y$$', '/* ; */ SELECT 1']);
  assert.equal(inferEditableSingleSelectSource('SELECT [order] FROM [table]').table, 'table');
});
