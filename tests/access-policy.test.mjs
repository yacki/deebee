import assert from 'node:assert/strict';
import test from 'node:test';
import { accessNavigation, selectedGrantAccounts, grantAccountFields, localDateTime, expiryTimestamp, optionLabel } from '../src/accessPolicy.ts';

const accounts = [
  { id:'reader',resource_id:'db1',tier:'normal',enabled:true },
  { id:'root',resource_id:'db1',tier:'privileged',enabled:true },
  { id:'other',resource_id:'db2',tier:'normal',enabled:true },
  { id:'pending',resource_id:'db1',tier:'normal',enabled:false },
  { id:'second',resource_id:'db1',tier:'normal',enabled:true },
];
test('navigation uses one vocabulary and nests resource accounts, credentials and settings',()=>{
  assert.deepEqual(accessNavigation.map(s=>s.name),['外部身份','系统账号','资源管理','资源授权','执行记录','审计日志','系统设置']);
  assert.equal(accessNavigation.some(s=>['accounts','api-keys','preview','identity-sources'].includes(s.id)),false);
  assert.equal(optionLabel('privileged'),'高权限账号');
});
test('existing two-account grants round trip without losing either account',()=>{
  const old={normal_account_id:'reader',privileged_account_id:'root',allow_privileged:true};
  assert.deepEqual(grantAccountFields(selectedGrantAccounts(old),accounts,'db1',true),old);
});
test('single high-permission account is explicit and never converted to normal',()=>{
  assert.deepEqual(grantAccountFields(['root'],accounts,'db1',true),{normal_account_id:'',privileged_account_id:'root',allow_privileged:true});
  assert.deepEqual(selectedGrantAccounts({normal_account_id:'reader',privileged_account_id:'root',allow_privileged:false}),['reader']);
});
test('cross-resource, disabled, empty and conflicting account selections are rejected',()=>{
  for(const ids of [[],['other'],['pending'],['reader','second'],['missing']]) assert.throws(()=>grantAccountFields(ids,accounts,'db1',true));
  assert.equal(grantAccountFields(['pending'],accounts,'db1',false).normal_account_id,'pending');
});
test('expiry is entered in local time, not Unix seconds',()=>{
  const seconds=Math.floor(Date.now()/60000)*60;
  assert.equal(expiryTimestamp(localDateTime(seconds)),seconds);
  assert.equal(expiryTimestamp(''),null);
  assert.throws(()=>expiryTimestamp('bad-date'));
});
