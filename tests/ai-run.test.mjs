import assert from 'node:assert/strict';
import test from 'node:test';
import { isNaturalLanguage } from '../src/aiRun.ts';

test('native SQL, comments and non-English identifiers stay on the native path', () => {
  for (const sql of ['SELECT * FROM 用户', '-- 查询\nSELECT 1', '/*说明*/ WITH t AS (SELECT 1) SELECT * FROM t', 'VACUUM orders', 'UPDATE users SET name=\'中文\'', '(SELECT 1)']) assert.equal(isNaturalLanguage(sql), false, sql);
});
test('plain requests and explicit question prefix select the AI path', () => {
  for (const value of ['查询最近 7 天的订单', 'List the latest 10 users', '? show me orders', '？列出所有用户']) assert.equal(isNaturalLanguage(value), true, value);
});
test('shell preserves commands with Chinese arguments and detects requests', () => {
  for (const value of ['git status', 'find . -type f -name 中文.txt', 'echo 你好']) assert.equal(isNaturalLanguage(value, true), false, value);
  for (const value of ['列出大文件', 'show me disk usage', '? find files']) assert.equal(isNaturalLanguage(value, true), true, value);
});
