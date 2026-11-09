"""Role authorization acceptance: complete paths, lifecycle, migration and revocation."""
from __future__ import annotations

import asyncio
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect

from deebee.access.api import install_access
from deebee.access.configuration import import_configuration, NAMES
from deebee.access.executions import ExecutionManager
from deebee.access.approvals import ApprovalPolicy, Decision
from deebee.access.models import AccessError
from deebee.access.service import public_entity
from deebee.access.store import AccessStore
from deebee.config import settings
from deebee.security import issue_token
from test_access_control import service, save, principal, published, identity


def role(service, name='Production inspection'):
    return save(service, 'roles', {'name': name, 'description': 'Resource access only'})


def membership(service, p, r, **values):
    return save(service, 'memberships', {'principal_id': p['id'], 'role_id': r['id'], **values})


def grant(service, r, resource, accounts, **values):
    return save(service, 'grants', {'role_id': r['id'], 'resource_id': resource['id'], 'normal_account_id': accounts[0], 'actions': ['resources:read', 'ssh:exec', 'ssh:inspect', 'db:query', 'db:write', 'k8s:read', 'k8s:write', 'privilege:use'], **values})


def patch(service, kind, old, **values):
    return save(service, kind, {'expected_version': old['version'], **values}, old['id'])


def test_many_people_inherit_many_roles_and_keep_direct_grants(service):
    people = [principal(service, str(i)) for i in range(3)]
    roles = [role(service, str(i)) for i in range(2)]
    resources = [published(service) for _ in range(3)]
    for r, (resource, accounts) in zip(roles, resources):
        grant(service, r, resource, accounts)
        for p in people:
            membership(service, p, r)
    for p in people:
        ctx, _ = identity(service, p['id'])
        assert len(service.resources(ctx)) == 2
        assert {r['name'] for r in service.me(ctx)['roles']} == {'0', '1'}
    p = people[0]
    resource, accounts = resources[2]
    save(service, 'grants', {'principal_id': p['id'], 'resource_id': resource['id'], 'normal_account_id': accounts[0]})
    ctx, _ = identity(service, p['id'])
    assert len(service.resources(ctx)) == 3
    assert service.projected(ctx, resource['id'])['access_modes'][0]['source'] == 'direct'


@pytest.mark.parametrize('layer,reason', [(layer, reason) for layer in ['membership','role','grant'] for reason in ['disabled','expired','future'] if layer != 'role' or reason == 'disabled'])
def test_all_authorization_layers_gate_access(service, layer, reason):
    p, r = principal(service), role(service)
    resource, accounts = published(service)
    m = membership(service, p, r)
    g = grant(service, r, resource, accounts)
    ctx, _ = identity(service, p['id'])
    option = service.projected(ctx, resource['id'])['access_modes'][0]['access_option_id']
    kind, old = {'membership': ('memberships', m), 'role': ('roles', r), 'grant': ('grants', g)}[layer]
    values = {'enabled': False} if reason == 'disabled' else {'starts_at': time.time() + 100} if reason == 'future' else {'expires_at': time.time() - 1}
    # Simulate time passing without bypassing the read-side authorization checks.
    service.store.put(kind, old['id'], old | values, old['version'])
    assert service.resources(ctx) == []
    with pytest.raises(AccessError):
        service.authorize(ctx, resource['id'], access_option_id=option)


def test_distinct_accounts_require_selection_and_selector_is_identity_bound(service):
    p, other = principal(service), principal(service, 'Other')
    resource, accounts = published(service)
    roles = [role(service, str(i)) for i in range(2)]
    second = save(service, 'accounts', {'name': 'Another reader', 'resource_id': resource['id'], 'username': 'another', 'password': 'secret'})
    raw = service.store.get('accounts', second['id'])
    raw = service.store.put('accounts', raw['id'], raw | {'test_result': {'connected': True, 'normal_safe': True, 'resource_version': resource['version']}}, raw['version'])
    second = patch(service, 'accounts', raw, enabled=True, permission_confirmed=True, permission_note='Unit fixture')
    for r, account in zip(roles, [accounts[0], second['id']]):
        membership(service, p, r)
        membership(service, other, r)
        grant(service, r, resource, [account])
    ctx, _ = identity(service, p['id'])
    other_ctx, _ = identity(service, other['id'])
    options = service.projected(ctx, resource['id'])['access_modes']
    assert len(options) == 2 and {o['username'] for o in options} == {'normal', 'another'}
    with pytest.raises(AccessError) as error:
        service.authorize(ctx, resource['id'])
    assert error.value.code == 'ACCESS_SELECTION_REQUIRED'
    for option in options:
        assert service.authorize(ctx, resource['id'], access_option_id=option['access_option_id'])[1]['username'] == option['username']
        with pytest.raises(AccessError):
            service.authorize(other_ctx, resource['id'], access_option_id=option['access_option_id'])


def test_accounts_actions_privilege_and_limits_cannot_be_spliced_between_roles(service):
    p = principal(service)
    resource, accounts = published(service, 'ssh')
    reader, privileged = role(service, 'Command operator'), role(service, 'Privileged inspector')
    membership(service, p, reader)
    membership(service, p, privileged)
    grant(service, reader, resource, accounts, actions=['resources:read', 'ssh:exec'], limits={'timeout_seconds': 9, 'max_rows': 7, 'max_output_bytes': 2048})
    grant(service, privileged, resource, accounts, normal_account_id='', privileged_account_id=accounts[1], allow_privileged=True, actions=['resources:read', 'ssh:inspect', 'privilege:use'])
    ctx, _ = identity(service, p['id'])
    options = service.projected(ctx, resource['id'])['access_modes']
    high = next(o for o in options if o['mode'] == 'privileged')
    low = next(o for o in options if o['mode'] == 'normal')
    assert low['limits']['timeout_seconds'] == 9 and low['limits']['max_rows'] == 7
    assert 'ssh.exec' not in high['actions']
    with pytest.raises(AccessError):
        service.authorize(ctx, resource['id'], 'privileged', 'ssh:exec', high['access_option_id'])
    restricted, _ = identity(service, p['id'], {'resources:read', 'ssh:exec', 'ssh:inspect'})
    assert all(o['mode'] == 'normal' for o in service.projected(restricted, resource['id'])['access_modes'])
    other, _ = published(service)
    key = service.create_key({'name': 'limited', 'principal_id': p['id'], 'resource_ids': [other['id']]}, 'admin')['key']
    restricted = asyncio.run(service.auth.authenticate({'x-deebee-api-key': key}))
    assert service.resources(restricted) == []


def test_revoked_selected_path_does_not_fall_back_to_another_role(service):
    p = principal(service)
    resource, accounts = published(service)
    roles = [role(service, str(i)) for i in range(2)]
    members = [membership(service, p, r) for r in roles]
    for r in roles:
        grant(service, r, resource, accounts)
    ctx, _ = identity(service, p['id'])
    selected = service.projected(ctx, resource['id'])['access_modes'][0]
    member = next(m for m in members if m['role_id'] == selected['role_id'])
    patch(service, 'memberships', member, enabled=False)
    assert len(service.projected(ctx, resource['id'])['access_modes']) == 1
    with pytest.raises(AccessError):
        service.authorize(ctx, resource['id'], access_option_id=selected['access_option_id'])


@pytest.mark.parametrize('kind', ['memberships', 'roles', 'grants', 'accounts', 'resources'])
def test_editing_authorization_path_invalidates_old_selection(service, kind):
    p, r = principal(service), role(service)
    resource, accounts = published(service)
    m, g = membership(service, p, r), grant(service, r, resource, accounts)
    ctx, _ = identity(service, p['id'])
    option = service.projected(ctx, resource['id'])['access_modes'][0]['access_option_id']
    old = {'memberships': m, 'roles': r, 'grants': g, 'accounts': service.store.get('accounts', accounts[0]), 'resources': resource}[kind]
    service.store.put(kind, old['id'], old, old['version'])
    with pytest.raises(AccessError):
        service.authorize(ctx, resource['id'], access_option_id=option)


def test_role_validation_uniqueness_and_cross_resource_binding(service):
    p, r = principal(service), role(service)
    with pytest.raises(AccessError):
        role(service, r['name'].upper())
    resource, accounts = published(service)
    other, _ = published(service)
    for body in ({'principal_id': p['id'], 'role_id': r['id']}, {'principal_id': '', 'role_id': ''}):
        with pytest.raises(ValidationError):
            save(service, 'grants', body | {'resource_id': resource['id']})
    with pytest.raises(AccessError):
        grant(service, r, other, accounts)
    membership(service, p, r)
    default_grant = save(service, 'grants', {'role_id': r['id'], 'resource_id': resource['id'], 'normal_account_id': accounts[0]})
    assert default_grant['actions'] == ['resources:read']
    with pytest.raises(AccessError):
        membership(service, p, r)
    with pytest.raises(ValidationError):
        membership(service, principal(service, 'Future'), r, starts_at=30, expires_at=20)


def test_clone_delete_and_audit_preserve_history(service):
    p, r = principal(service), role(service)
    resource, accounts = published(service)
    membership(service, p, r)
    g = grant(service, r, resource, accounts)
    cloned = asyncio.run(service.clone_role(r['id'], {'name': 'Copy', 'expected_version': r['version']}, 'admin'))
    assert not cloned['enabled']
    copies = [g for g in service.store.list('grants') if g.get('role_id') == cloned['id']]
    assert len(copies) == 1 and not copies[0]['enabled']
    assert not any(m['role_id'] == cloned['id'] for m in service.store.list('memberships'))
    with pytest.raises(AccessError):
        service.delete_role(r['id'], {'expected_version': r['version'], 'confirm_name': 'wrong'}, 'admin')
    service.delete_role(r['id'], {'expected_version': r['version'], 'confirm_name': r['name']}, 'admin')
    assert service.store.get('grants', g['id'])['deleted_at']
    assert service.store.get('roles', r['id'])['name'] == r['name']
    assert any(a['action'] == 'roles.delete' for a in service.store.audits())
    with pytest.raises(AccessError):
        patch(service, 'roles', service.store.get('roles', r['id']), enabled=True)


def test_config_v2_preview_rollback_roundtrip_and_v1_compatibility(service, tmp_path):
    p, r = principal(service), role(service)
    resource, accounts = published(service)
    membership(service, p, r)
    grant(service, r, resource, accounts)
    exported = {'version': 2, **{name: [public_entity(v) for v in service.store.list(kind)] for name, kind in NAMES.items()}}
    assert asyncio.run(import_configuration(service, exported, 'admin', preview=True))['updated'] == 0
    assert 'secret' not in str(exported)
    assert asyncio.run(import_configuration(service, {'version': 1, 'principals': [{'id': 'legacy_person', 'name': 'legacy'}]}, 'admin', preview=False))['updated'] == 1
    preview = {'version': 2, 'roles': [{'id': 'preview_role', 'name': 'Preview'}], 'role-memberships': [{'id': 'preview_member', 'principal_id': p['id'], 'role_id': 'preview_role'}]}
    asyncio.run(import_configuration(service, preview, 'admin', preview=True))
    assert service.store.get('roles', 'preview_role', required=False) is None
    bad = preview | {'role-memberships': [{'id': 'bad_member', 'role_id': 'preview_role', 'principal_id': 'missing'}]}
    with pytest.raises(AccessError):
        asyncio.run(import_configuration(service, bad, 'admin', preview=False))
    assert service.store.get('roles', 'preview_role', required=False) is None
    directory = service.store.directory
    service.store.close()
    service.store = AccessStore(directory)
    service.auth.store = service.store
    service.approvals = ApprovalPolicy(service.store)
    assert service.store.get('roles', r['id'])['name'] == r['name']
    ctx, _ = identity(service, p['id'])
    assert service.resources(ctx)
    service.store.close()


@pytest.mark.parametrize('change', ['remove_member', 'disable_role', 'disable_grant', 'expire_member'])
def test_running_execution_is_cancelled_and_records_role_source(service, monkeypatch, change):
    p, r = principal(service), role(service)
    resource, accounts = published(service, 'ssh')
    m, g = membership(service, p, r), grant(service, r, resource, accounts)
    ctx, _ = identity(service, p['id'])
    async def run():
        manager = ExecutionManager(service)
        started = asyncio.Event()
        async def ssh(resource, account, secret, command, limit, handle, **kwargs):
            started.set()
            while not handle.cancelled.is_set():
                await asyncio.sleep(.01)
            return {"stdout": "", "stderr": "", "exit_code": 0}
        monkeypatch.setattr('deebee.access.executions.ssh_execute', ssh)
        await manager.start()
        try:
            record = await manager.submit(ctx, 'ssh.exec', {'resource_id': resource['id'], 'command': 'sleep 60', 'idempotency_key': change})
            await manager.decide(record['id'], Decision(decision='approve', comment='Allow fixture process for revocation test', request_hash=manager.get_body(record['id'])['request_hash']), 'admin')
            await asyncio.wait_for(started.wait(), 2)
            assert record['role_name'] == r['name'] and record['grant_id'] == g['id']
            if change == 'remove_member':
                await service.save('memberships', {'expected_version': m['version'], 'enabled': False}, 'admin', m['id'])
            elif change == 'disable_role':
                await service.save('roles', {'expected_version': r['version'], 'enabled': False}, 'admin', r['id'])
            elif change == 'disable_grant':
                await service.save('grants', {'expected_version': g['version'], 'enabled': False}, 'admin', g['id'])
            else:
                service.store.put('memberships', m['id'], m | {'expires_at': time.time() + .1}, m['version'])
            for _ in range(50):
                if manager.get_body(record['id'])['status'] in {'cancelled', 'unknown', 'failed'}:
                    break
                await asyncio.sleep(.05)
            assert manager.get_body(record['id'])['status'] in {'cancelled', 'unknown', 'failed'}
            audit = next(a for a in service.store.audits() if a['action'] == 'execution.queued')
            assert audit['detail']['role_id'] == r['id']
        finally:
            await manager.close()
    asyncio.run(run())


def test_admin_rest_batches_are_atomic_and_role_does_not_grant_admin(tmp_path, monkeypatch):
    monkeypatch.setenv('DEEBEE_RECORDINGS_DIR', str(tmp_path / 'recordings'))
    app = FastAPI()
    install_access(app, tmp_path / 'access')
    service = app.state.access
    with TestClient(app) as client:
        admin = {'Authorization': 'Bearer ' + issue_token(settings.admin_user)}
        def post(path, body):
            return client.post('/api/admin/v1/' + path, headers=admin, json=body)
        p = post('principals', {'name': 'Service', 'kind': 'service'}).json()
        r = post('roles', {'name': 'System administrator'}).json()
        result = post('role-memberships/batch', {'changes': [{'principal_id': p['id'], 'role_id': r['id']}, {'principal_id': 'missing', 'role_id': r['id']}]})
        assert result.status_code == 404 and service.store.list('memberships') == []
        assert post('roles/' + r['id'] + '/members', {'principal_ids': [p['id']], 'expected_version': r['version']}).is_success
        ctx, key = identity(service, p['id'])
        headers = {'x-deebee-api-key': key}
        assert client.get('/api/admin/v1/roles', headers=headers).status_code == 403
        assert client.get('/api/v1/me', headers=headers).json()['roles'][0]['name'] == r['name']
        resource, accounts = published(service)
        valid = {'role_id': r['id'], 'resource_id': resource['id'], 'normal_account_id': accounts[0]}
        assert post('access-grants/batch', {'grants': [valid, valid | {'resource_id': 'missing'}]}).status_code == 404
        assert service.store.list('grants') == []
        assert post('access-grants/batch', {'grants': [valid]}).status_code == 201
        config = client.get('/api/admin/v1/access-config/export', headers=admin).json()
        assert config['version'] == 2 and config['roles'] and config['role-memberships']
        assert client.get('/api/v1/resources', headers=headers).json()['resources'][0]['access_modes'][0]['role_name'] == r['name']


def test_browser_sessions_enforce_cookie_csrf_origin_owner_and_revocation(tmp_path, monkeypatch):
    monkeypatch.setenv('DEEBEE_ACCESS_ALLOW_INSECURE_LOCAL', '1')
    monkeypatch.setenv('DEEBEE_RECORDINGS_DIR', str(tmp_path / 'recordings'))
    seen = []
    async def handler(ws, resource_id):
        session = ws.state.access_session
        await ws.accept()
        seen.append(session.profile)
        try:
            while True:
                await ws.receive_text()
                await session.check()
                await ws.send_text('allowed')
        except AccessError:
            await ws.close(code=4403)
    app = FastAPI()
    install_access(app, tmp_path / 'access', session_handlers={'ssh': handler, 'rdp': handler})
    service = app.state.access
    # This regression exercises unrestricted sessions with approval explicitly off.
    for rule in service.approvals.rules():
        if rule['protocol'] == 'ssh':
            service.approvals.save({'expected_version': rule['version'], 'enabled': False}, 'admin', rule['id'])
    p = save(service, 'principals', {'name': 'Human', 'username': 'role_user', 'password': 'test_password_123'})
    r = role(service)
    resource, accounts = published(service, 'ssh')
    m = membership(service, p, r)
    other = save(service, 'principals', {'name': 'Other human', 'username': 'other_role_user', 'password': 'test_password_123'})
    membership(service, other, r)
    grant(service, r, resource, accounts, actions=['resources:read', 'ssh:connect'])
    with TestClient(app) as client:
        login = client.post('/api/v1/local/login', json={'username': 'role_user', 'password': 'test_password_123'})
        headers = {'x-csrf-token': login.json()['csrf']}
        client.cookies.set('deebee_access', login.cookies.get('deebee_access'), path='/')
        selector = client.get('/api/v1/resources').json()['resources'][0]['access_modes'][0]['access_option_id']
        body = {'resource_id': resource['id'], 'access_option_id': selector}
        assert client.post('/api/v1/sessions', json=body).status_code == 403
        def ticket():
            response = client.post('/api/v1/sessions', json=body, headers=headers)
            assert response.status_code == 201, response.text
            return '/api/v1/sessions/' + response.json()['id'] + '/ssh'
        path = ticket()
        owner_cookie = client.cookies.get('deebee_access', path='/')
        other_login = client.post('/api/v1/local/login', json={'username': 'other_role_user', 'password': 'test_password_123'})
        client.cookies.set('deebee_access', other_login.cookies.get('deebee_access'), path='/')
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(path, headers={'origin': 'http://testserver'}):
                pass
        client.cookies.set('deebee_access', owner_cookie, path='/')
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(path, headers={'origin': 'https://attacker.invalid'}):
                pass
        # A rejected connection must not consume the legitimate owner's ticket.
        with client.websocket_connect(path, headers={'origin': 'http://testserver'}) as ws:
            ws.send_text('id'); assert ws.receive_text() == 'allowed'
            assert seen[0].user == 'normal' and seen[0].id == resource['id']
            event = next(a for a in service.store.audits() if a['action'] == 'ssh.session.open')
            assert event['actor_name'] == 'Human' and event['actor_kind'] == 'human' and event['credential_id']
            admin = {'Authorization': 'Bearer ' + issue_token(settings.admin_user)}
            response = client.patch('/api/admin/v1/role-memberships/' + m['id'], json={'expected_version': m['version'], 'enabled': False}, headers=admin)
            assert response.status_code == 200
            ws.send_text('id')
            with pytest.raises(WebSocketDisconnect):
                ws.receive_text()
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(path, headers={'origin': 'http://testserver'}):
                pass


def test_queued_execution_cannot_start_after_membership_revocation(service, monkeypatch):
    p, r = principal(service), role(service)
    resource, accounts = published(service, 'ssh')
    m = membership(service, p, r)
    grant(service, r, resource, accounts)
    ctx, _ = identity(service, p['id'])
    calls = []
    async def driver(*args, **kwargs):
        calls.append(True)
        return {'exit_code': 0}
    monkeypatch.setattr('deebee.access.executions.ssh_execute', driver)
    async def run():
        manager = ExecutionManager(service)
        record = await manager.submit(ctx, 'ssh.exec', {'resource_id': resource['id'], 'command': 'id', 'idempotency_key': 'queued'})
        await service.save('memberships', {'expected_version': m['version'], 'enabled': False}, 'admin', m['id'])
        await asyncio.gather(*list(manager.tasks.values()))
        assert manager.get_body(record['id'])['status'] == 'failed'
        assert calls == []
        await manager.close()
    asyncio.run(run())


def test_roles_preserve_kubernetes_namespace_and_explicit_restart_boundaries(service, monkeypatch):
    p, r = principal(service), role(service)
    resource, accounts = published(service, 'k8s')
    membership(service, p, r)
    grant(service, r, resource, accounts, privileged_account_id=accounts[1], allow_privileged=True)
    ctx, _ = identity(service, p['id'])
    called = []
    async def driver(resource, account, secret, body, limit, handle):
        called.append((account['id'], body['operation'], body['namespace']))
        return {'items': [{'metadata': {'name': 'example'}}]}
    monkeypatch.setattr('deebee.access.executions.kubernetes_execute', driver)
    async def run():
        manager = ExecutionManager(service)
        common = {'resource_id': resource['id'], 'namespace': 'default', 'idempotency_key': 'k8s-read'}
        record = await manager.submit(ctx, 'k8s.read', common)
        await asyncio.gather(*list(manager.tasks.values()))
        assert manager.get_body(record['id'])['status'] == 'succeeded'
        assert record['role_id'] == r['id'] and called[0][0] == accounts[0]
        with pytest.raises(AccessError):
            await manager.submit(ctx, 'k8s.read', common | {'namespace': 'forbidden', 'idempotency_key': 'denied'})
        restart = common | {'operation': 'restart', 'kind': 'deployments', 'name': 'example', 'idempotency_key': 'restart'}
        with pytest.raises(AccessError):
            await manager.submit(ctx, 'k8s.restart', restart)
        record = await manager.submit(ctx, 'k8s.restart', restart | {'mode': 'privileged'})
        await asyncio.gather(*list(manager.tasks.values()))
        assert manager.get_body(record['id'])['status'] == 'succeeded' and called[-1][0] == accounts[1]
        await manager.close()
    asyncio.run(run())


def test_v1_database_upgrade_retains_direct_grants_and_adds_independent_role_grants(service):
    p = principal(service)
    resource, accounts = published(service)
    direct = save(service, 'grants', {'principal_id': p['id'], 'resource_id': resource['id'], 'normal_account_id': accounts[0]})
    directory = service.store.directory
    # Reproduce the pre-role index and JSON shape from V1 on disk.
    service.store.db.executescript('''
        DROP INDEX unique_principal_grant;
        DROP INDEX unique_role_grant;
        UPDATE entities SET body=json_remove(body,'$.role_id','$.starts_at') WHERE kind='grants';
        CREATE UNIQUE INDEX unique_grant ON entities(json_extract(body,'$.principal_id'),json_extract(body,'$.resource_id')) WHERE kind='grants';
    ''')
    service.store.close()
    service.store = AccessStore(directory)
    service.auth.store = service.store
    service.approvals = ApprovalPolicy(service.store)
    try:
        ctx, _ = identity(service, p['id'])
        assert service.projected(ctx, resource['id'])['access_modes'][0]['source'] == 'direct'
        r = role(service)
        membership(service, p, r)
        grant(service, r, resource, accounts)
        modes = service.projected(ctx, resource['id'])['access_modes']
        assert len(modes) == 2 and {m['source'] for m in modes} == {'direct', 'role'}
        assert service.store.get('grants', direct['id'])['normal_account_id'] == accounts[0]
        with pytest.raises(AccessError):
            save(service, 'grants', {'principal_id': p['id'], 'resource_id': resource['id'], 'normal_account_id': accounts[0]})
    finally:
        service.store.close()


def test_selected_role_limits_database_schema_rows_bytes_and_permission(service, monkeypatch):
    p, r = principal(service), role(service)
    resource, accounts = published(service)
    membership(service, p, r)
    grant(service, r, resource, accounts, actions=['resources:read', 'db:query'], limits={'timeout_seconds': 2, 'max_rows': 2, 'max_output_bytes': 1024})
    ctx, _ = identity(service, p['id'])
    rows = [{'schema': 'public', 'table': 'items', 'column': str(i), 'type': 'text'} for i in range(8)]
    monkeypatch.setattr('deebee.access.executions.database_schema', lambda *args: rows)
    manager = ExecutionManager(service)
    result = asyncio.run(manager.schema(ctx, resource['id']))
    assert len(result['columns']) == 2 and result['truncated']
    monkeypatch.setattr('deebee.access.executions.database_schema', lambda *args: [{'column': 'x' * 2048}])
    with pytest.raises(AccessError) as error:
        asyncio.run(manager.schema(ctx, resource['id']))
    assert error.value.code == 'OUTPUT_LIMIT'


def test_rdp_account_verification_is_supported_and_never_claims_readonly(service, monkeypatch):
    resource = save(service, 'resources', {'name': 'Desktop', 'type': 'rdp', 'host': 'desktop.internal', 'port': 3389, 'enabled': True})
    account = save(service, 'accounts', {'name': 'Desktop operator', 'resource_id': resource['id'], 'username': 'operator', 'password': 'secret', 'tier': 'privileged'})
    async def inspect(resource, account, secret):
        assert resource['type'] == 'rdp' and secret['password'] == 'secret'
        return {'connected': True, 'normal_safe': False, 'permission_summary': {'manual_review_required': True}}
    monkeypatch.setattr('deebee.access.executions.inspect_account', inspect)
    manager = ExecutionManager(service)
    tested = asyncio.run(manager.test_account(account['id'], 'admin'))
    assert tested['test_result']['connected'] and not tested['test_result']['normal_safe']
    enabled = patch(service, 'accounts', tested, enabled=True, permission_confirmed=True, permission_note='Manually reviewed')
    assert enabled['enabled']


def test_role_activity_filtering_and_pagination_do_not_depend_on_global_recent_logs(tmp_path, monkeypatch):
    monkeypatch.setenv('DEEBEE_RECORDINGS_DIR', str(tmp_path / 'recordings'))
    app = FastAPI()
    install_access(app, tmp_path / 'access')
    r = role(app.state.access)
    for i in range(105):
        app.state.access.store.audit('admin', 'roles.update', r['id'], {'revision': i})
    for i in range(505):
        app.state.access.store.audit('admin', 'unrelated', 'another-role', {'revision': i})
    with TestClient(app) as client:
        admin = {'Authorization': 'Bearer ' + issue_token(settings.admin_user)}
        first = client.get('/api/admin/v1/roles/' + r['id'] + '/activities', headers=admin).json()
        assert len(first['items']) == 100 and first['next_before']
        second = client.get('/api/admin/v1/roles/' + r['id'] + '/activities', params={'before': first['next_before']}, headers=admin).json()
        assert len(second['items']) == 6 and second['next_before'] is None
        assert not {a['id'] for a in first['items']} & {a['id'] for a in second['items']}
        assert all(a['object_id'] == r['id'] for a in first['items'] + second['items'])
