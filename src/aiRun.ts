export type PreparedRun = { code: string; message: string; auto_run: boolean; requires_confirmation: boolean };
export type RunMeta = { generated: true; readOnly: boolean };

/** Recognize native statements before considering natural language. */
export function isNaturalLanguage(value: string, shell = false): boolean {
  const text = value.replace(/^(?:\s|--[^\n]*(?:\n|$)|\/\*[\s\S]*?\*\/)+/, '').trim();
  if (!text) return false;
  if (/^\(/.test(text)) return false;
  if (text.startsWith('?') || text.startsWith('？')) return true;
  if (shell) return /^[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}]/u.test(text) || /^(?:please\s|show me\s|list (?:all|the)\s|find (?:all|the|large)\s)/i.test(text);
  return !/^(?:select|with|insert|update|delete|create|alter|drop|truncate|show|describe|desc|explain|use|set|begin|start|commit|rollback|savepoint|release|grant|revoke|call|exec|execute|declare|do|copy|vacuum|analyze|analyse|reindex|refresh|merge|replace|values|table|pragma|load|lock|unlock|kill|flush|optimize|repair|check|attach|detach)\b/i.test(text);
}
