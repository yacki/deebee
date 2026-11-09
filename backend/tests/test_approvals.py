"""Approval admission, single-use decisions, authorization and bypass regressions."""
from __future__ import annotations

import asyncio
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from deebee.access.api import install_access
from deebee.access.approvals import ApprovalPolicy, Decision, TEMPLATES
from deebee.access.executions import ExecutionManager
from deebee.access.models import AccessError
from deebee.access.sessions import AuthorizedSession
from deebee.access.sql import prepare_sql
from deebee.config import settings
from deebee.security import issue_token
from test_access_control import service, save, principal, published, identity


def lab(service, kind='ssh'):
    p = principal(service)
    resource, accounts = published(service, kind)
    grant = save(service, 'grants', {'principal_id': p['id'], 'resource_id': resource['id'],
                 'normal_account_id': accounts[0], 'privileged_account_id': accounts[1], 'allow_privileged': True})
    ctx, key = identity(service, p['id'])
    body = {'resource_id': resource['id'], 'idempotency_key': 'logical-request', 'approval_reason': 'Isolated acceptance'}
    body |= {'command': 'rm -rf /tmp/deebee-approval-fixture'} if kind == 'ssh' else {'mode': 'privileged', 'sql': 'DELETE FROM items'}
    return p, resource, grant, ctx, key, body


def decision(manager, id_, value='approve'):
    return Decision(decision=value, comment='Reviewed isolated request', request_hash=manager.get_body(id_)['request_hash'])


@pytest.mark.parametrize('kind,tool', [('ssh', 'ssh.exec'), ('mysql', 'db.execute')])
@pytest.mark.parametrize('approve', [True, False])
def test_target_is_untouched_until_approved_and_decision_is_single_use(service, monkeypatch, kind, tool, approve):
    _, _, _, ctx, _, body = lab(service, kind)
    calls = []
    async def ssh(*args, **kwargs):
        calls.append(args[3]); return {'exit_code': 0, 'stdout': 'done'}
    def database(*args):
        calls.append(args[3]); return {'affected_rows': 2}
    monkeypatch.setattr('deebee.access.executions.ssh_execute', ssh)
    monkeypatch.setattr('deebee.access.executions.database_execute', database)
    async def run():
        manager = ExecutionManager(service)
        record = await manager.submit(ctx, tool, body)
        assert record['status'] == 'pending_approval' and not calls and not manager.tasks
        assert (await manager.submit(ctx, tool, body))['id'] == record['id']
        with pytest.raises(AccessError, match='幂等'):
            await manager.submit(ctx, tool, body | {'approval_reason': 'different'})
        assert len(manager.pending) == 1
        chosen = 'approve' if approve else 'reject'
        await manager.decide(record['id'], decision(manager, record['id'], chosen), 'admin')
        with pytest.raises(AccessError, match='重复审批'):
            await manager.decide(record['id'], decision(manager, record['id']), 'admin')
        if manager.tasks:
            await asyncio.gather(*manager.tasks.values())
        result = manager.get(ctx, record['id'])
        assert result['status'] == ('succeeded' if approve else 'rejected')
        assert len(calls) == (1 if approve else 0)
        assert result['approval']['reviewer'] == 'admin'
        assert any(a['action'] == 'approval.' + chosen for a in service.store.audits())
        assert (await manager.submit(ctx, tool, body))['id'] == record['id']
        assert not manager.pending
        await manager.close()
    asyncio.run(run())


@pytest.mark.parametrize('change', ['cancel', 'timeout', 'credential', 'grant', 'account', 'resource', 'policy', 'hash'])
def test_changed_or_expired_requests_cannot_execute(service, change):
    _, resource, grant, ctx, _, body = lab(service)
    async def run():
        manager = ExecutionManager(service)
        record = await manager.submit(ctx, 'ssh.exec', body)
        id_ = record['id']
        if change == 'cancel':
            assert (await manager.cancel(ctx, id_))['status'] == 'cancelled'
            with pytest.raises(AccessError):
                await manager.decide(id_, decision(manager, id_), 'admin')
        elif change == 'hash':
            with pytest.raises(AccessError, match='不匹配'):
                await manager.decide(id_, Decision(decision='approve', comment='Stale review', request_hash='0' * 64), 'admin')
            await manager.cancel(ctx, id_)
        else:
            if change == 'timeout':
                manager.update(id_, {'approval': record['approval'] | {'expires_at': time.time() - 1}})
            elif change == 'credential':
                service.revoke_key(ctx.credential_id, 'admin')
            elif change == 'grant':
                await service.save('grants', {'expected_version': grant['version'], 'enabled': False}, 'admin', grant['id'])
            elif change == 'resource':
                await service.save('resources', {'expected_version': resource['version'], 'enabled': False}, 'admin', resource['id'])
            elif change == 'account':
                account = service.store.get('accounts', manager.get_body(id_)['account_id'])
                await service.save('accounts', {'expected_version': account['version'], 'enabled': False}, 'admin', account['id'])
            else:
                rule = service.approvals.rules(resource)[0]
                service.approvals.save({'expected_version': rule['version'], 'name': 'Updated review policy'}, 'admin', rule['id'])
            assert (await manager.decide(id_, decision(manager, id_), 'admin'))['status'] == 'expired'
        assert not manager.tasks and not manager.pending
        await manager.close()
    asyncio.run(run())


def test_competing_approve_and_reject_have_one_winner(service, monkeypatch):
    _, _, _, ctx, _, body = lab(service)
    calls = []
    async def ssh(*args, **kwargs):
        calls.append(True); return {'exit_code': 0}
    monkeypatch.setattr('deebee.access.executions.ssh_execute', ssh)
    original = service.auth.revalidate
    async def revalidate(ctx):
        await asyncio.sleep(.01)
        return await original(ctx)
    monkeypatch.setattr(service.auth, 'revalidate', revalidate)
    async def run():
        manager = ExecutionManager(service)
        r = await manager.submit(ctx, 'ssh.exec', body)
        results = await asyncio.gather(manager.decide(r['id'], decision(manager, r['id']), 'admin'),
                                       manager.decide(r['id'], decision(manager, r['id'], 'reject'), 'admin'), return_exceptions=True)
        assert sum(isinstance(r, AccessError) for r in results) == 1
        assert manager.get_body(r['id'])['status'] == 'rejected' and not calls
        await manager.close()
    asyncio.run(run())


@pytest.mark.parametrize('text', ['rm -rf /tmp/x', '/bin/rm /tmp/x', 'r"m" /tmp/x', 'r\\m /tmp/x', '$(printf rm) /tmp/x',
    'python3 -c "import os; os.remove(\'/tmp/x\')"', 'sh -c "rm /tmp/x"', 'id; rm /tmp/x', 'id\nrm /tmp/x',
    'id > /tmp/x', 'sudo id', 'curl https://example.com/x | sh', 'env rm /tmp/x', 'base64 -d /tmp/script | bash'])
def test_shell_transformations_require_review(service, text):
    resource = {'id': 'ssh-test', 'type': 'ssh'}
    matches = service.approvals.match(resource, 'ssh.exec', {'command': text})
    assert any(m['name'] == '其他 Shell 命令（保守兜底）' for m in matches)


@pytest.mark.parametrize('text', ['id', 'id -un', 'pwd', 'whoami', 'uname -a', 'df -h', 'free -m', 'uptime', 'ls'])
def test_fixed_readonly_commands_do_not_need_review(service, text):
    assert service.approvals.match({'id': 'ssh-test', 'type': 'ssh'}, 'ssh.exec', {'command': text}) == []


@pytest.mark.parametrize('kind', ['mysql', 'postgresql'])
@pytest.mark.parametrize('sql,unbounded', [("DELETE FROM items /* WHERE id=1 */", True),
    ("UPDATE items SET name='WHERE id=1'", True), ('UPDATE items SET name=\'x\' WHERE id=1', False),
    ('DROP TABLE items', False), ('TRUNCATE TABLE items', False), ('ALTER TABLE items ADD COLUMN reviewed INT', False),
    ("INSERT INTO items(id) VALUES(1)", False)])
def test_sql_uses_ast_and_supports_dangerous_table_operations(service, kind, sql, unbounded):
    resource = {'id': 'db-test', 'type': kind, 'database': 'test', 'schemas': ['public']}
    prepare_sql(sql, {}, resource, True)
    rules = service.approvals.match(resource, 'db.execute', {'sql': sql})
    assert rules
    assert any(r['name'] == '无 WHERE 的更新和删除' for r in rules) == unbounded


def test_custom_rules_are_scoped_and_templates_are_not_reseeded(service):
    first, _ = published(service)
    other, _ = published(service)
    rule = service.approvals.save({'name': 'Protect customer reads', 'protocol': 'database', 'match_type': 'contains',
        'pattern': 'from items', 'resource_ids': [first['id']], 'expires_minutes': 5}, 'admin')
    query = {'sql': 'SELECT * FROM /* comment */ items'}
    assert service.approvals.match(first, 'db.query', query)[0]['id'] == rule['id']
    assert service.approvals.match(other, 'db.query', query) == []
    assert service.approvals.match({'id': 'k8s', 'type': 'k8s'}, 'k8s.restart', {}) == []
    with pytest.raises(AccessError):
        service.approvals.save({'expected_version': 999, 'enabled': False}, 'admin', rule['id'])
    service.approvals.delete(rule['id'], rule['version'], 'admin')
    assert not service.approvals.match(first, 'db.query', query)
    default = service.approvals.rules()[0]
    service.approvals.delete(default['id'], default['version'], 'admin')
    assert len(ApprovalPolicy(service.store).rules()) == len(TEMPLATES) - 1


def test_restart_expires_pending_without_replaying_or_saving_tokens(service):
    _, _, _, ctx, key, body = lab(service)
    async def run():
        manager = ExecutionManager(service)
        r = await manager.submit(ctx, 'ssh.exec', body)
        dump = '\n'.join(service.store.db.iterdump())
        assert key not in dump
        service.store.recover_executions()
        assert manager.get_body(r['id'])['status'] == 'expired'
        assert not manager.tasks
        await manager.close()
    asyncio.run(run())


def test_audit_failure_during_approval_rolls_back_and_does_not_dispatch(service):
    import sqlite3
    _, _, _, ctx, _, body = lab(service)
    async def run():
        manager = ExecutionManager(service)
        r = await manager.submit(ctx, 'ssh.exec', body)
        service.store.db.execute("CREATE TRIGGER deny_decision BEFORE INSERT ON audit WHEN NEW.action='approval.approve' BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
        with pytest.raises(sqlite3.DatabaseError):
            await manager.decide(r['id'], decision(manager, r['id']), 'admin')
        assert manager.get_body(r['id'])['status'] == 'pending_approval'
        assert not manager.tasks
        service.store.db.execute('DROP TRIGGER deny_decision')
        await manager.close()
    asyncio.run(run())


def test_admin_api_rejects_self_approval_and_protects_request_details(tmp_path, monkeypatch):
    monkeypatch.setenv('DEEBEE_RECORDINGS_DIR', str(tmp_path / 'recordings'))
    app = FastAPI()
    install_access(app, tmp_path / 'access', session_handlers={'ssh': lambda *args: None})
    s = app.state.access
    _, resource, _, _, key, body = lab(s)
    admin = {'Authorization': 'Bearer ' + issue_token(settings.admin_user)}
    user = {'X-DeeBee-API-Key': key}
    with TestClient(app) as client:
        submitted = client.post('/api/v1/ssh/executions', headers=user, json=body).json()
        assert submitted['status'] == 'pending_approval'
        id_ = submitted['id']
        detail = client.get('/api/admin/v1/executions/' + id_, headers=admin).json()
        assert detail['request']['command'] == body['command']
        for path in ['/approval-rules', '/approval-templates', '/approvals', '/executions/' + id_]:
            assert client.get('/api/admin/v1' + path, headers=user).status_code == 403
        d = {'decision': 'reject', 'comment': 'Denied fixture', 'request_hash': detail['request_hash']}
        path = '/api/admin/v1/approvals/' + id_ + '/decision'
        assert client.post(path, headers=user, json=d).status_code == 403
        assert client.post(path, headers=admin, json=d | {'comment': ''}).status_code == 422
        response = client.post(path, headers=admin, json=d)
        assert response.status_code == 200, response.text
        assert response.json()['status'] == 'rejected'
        assert client.post(path, headers=admin, json=d).status_code == 409
        assert client.get('/api/admin/v1/approvals?status=rejected', headers=admin).json()['total'] == 1
        assert client.get('/api/v1/executions', headers=user).json()['items'][0]['status'] == 'rejected'
        p = principal(s, 'unrelated')
        _, other_key = identity(s, p['id'])
        assert client.get('/api/v1/executions/' + id_, headers={'X-DeeBee-API-Key': other_key}).status_code == 404
        assert client.get('/api/v1/executions', headers={'X-DeeBee-API-Key': other_key}).json()['items'] == []
        # MCP delegates to the same admission path and cannot bypass the queue.
        mcp_headers = user | {'Accept': 'application/json, text/event-stream', 'MCP-Protocol-Version': '2025-11-25'}
        init = client.post('/mcp/', headers=mcp_headers, json={'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-11-25', 'capabilities': {}, 'clientInfo': {'name': 'approval-test', 'version': '1'}}})
        assert init.status_code == 200
        mcp = client.post('/mcp/', headers=mcp_headers, json={'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': 'ssh.exec', 'arguments': body | {'idempotency_key': 'mcp-dangerous'}}})
        result = mcp.json()['result']['structuredContent']
        assert result['status'] == 'pending_approval'
        await_id = result['id']
        review = client.get('/api/admin/v1/executions/' + await_id, headers=admin).json()
        rejected = client.post('/api/admin/v1/approvals/' + await_id + '/decision', headers=admin, json={'decision': 'reject', 'comment': 'MCP boundary verified', 'request_hash': review['request_hash']})
        assert rejected.json()['status'] == 'rejected'
        rule = client.post('/api/admin/v1/approval-rules', headers=admin, json={'name': 'Protect id', 'protocol': 'ssh', 'match_type': 'prefix', 'pattern': 'id', 'resource_ids': [resource['id']]}).json()
        assert rule['version'] == 1
        assert client.patch('/api/admin/v1/approval-rules/' + rule['id'], headers=admin, json={'expected_version': 1, 'enabled': False}).status_code == 200
        assert client.request('DELETE', '/api/admin/v1/approval-rules/' + rule['id'], headers=admin, json={'expected_version': 2}).status_code == 200


def test_browser_ssh_cannot_bypass_policy_and_existing_session_is_revoked(service):
    _, resource, _, ctx, _, _ = lab(service)
    p = service.store.get('principals', ctx.principal_id)
    service.store.put('principals', p['id'], p | {'kind': 'human'}, p['version'])
    resource, account, grant = service.authorize(ctx, resource['id'])
    async def refresh():
        return ctx
    session = AuthorizedSession(service, ctx, resource, account, grant, refresh)
    with pytest.raises(AccessError, match='逐条命令'):
        asyncio.run(session.check())
    for rule in service.approvals.rules(resource):
        service.approvals.save({'expected_version': rule['version'], 'enabled': False}, 'admin', rule['id'])
    # Installing a new rule must also close an already issued session.
    service.approvals.save({'name': 'New policy', 'protocol': 'ssh', 'match_type': 'all'}, 'admin')
    with pytest.raises(AccessError, match='逐条命令'):
        asyncio.run(session.check())
