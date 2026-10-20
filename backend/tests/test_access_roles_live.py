"""Opt-in role acceptance with real SSH/MySQL/PostgreSQL and MCP I/O."""
import asyncio
import json
import time
import uuid

import httpx
import pytest

from test_access_live import lab, BASE, poll

pytestmark = pytest.mark.skipif(not BASE, reason='isolated access lab not requested')


@pytest.fixture(scope='module')
def role_lab(lab):
    admin = lab['admin']
    role = admin.post('/api/admin/v1/roles', json={'name': 'Role live ' + uuid.uuid4().hex[:8]}).json()
    response = admin.post('/api/admin/v1/roles/' + role['id'] + '/members', json={'principal_ids': [lab['principal']['id']], 'expected_version': role['version']})
    assert response.is_success, response.text
    member = response.json()['items'][0]
    for kind, grant in lab['grants'].items():
        response = admin.patch('/api/admin/v1/access-grants/' + grant['id'], json={'expected_version': grant['version'], 'principal_id': '', 'role_id': role['id'], 'actions': list(grant['actions']) + (['ssh:connect'] if kind == 'ssh' else [])})
        assert response.is_success, response.text
        lab['grants'][kind] = response.json()
    return lab | {'role': role, 'member': member}


@pytest.mark.parametrize('kind', ['ssh', 'mysql', 'postgresql'])
def test_real_role_account_execution_and_audit(role_lab, kind):
    lab = role_lab
    client, resource = lab['clients']['normal'], lab['resources'][kind]
    option = client.get('/api/v1/resources/' + resource['id']).json()['access_modes'][0]
    assert option['role_name'] == lab['role']['name']
    body = {'resource_id': resource['id'], 'access_option_id': option['access_option_id'], 'idempotency_key': uuid.uuid4().hex}
    body.update({'command': 'id -un'} if kind == 'ssh' else {'sql': 'SELECT name FROM items WHERE id=1'})
    response = client.post('/api/v1/ssh/executions' if kind == 'ssh' else '/api/v1/db/queries', json=body)
    assert response.status_code == 202, response.text
    result = poll(client, response.json()['execution_id'])
    assert result['status'] == 'succeeded' and result['role_id'] == lab['role']['id'], result
    if kind == 'ssh':
        assert result['result']['stdout'].strip() == 'lab_reader'
    else:
        assert result['result']['rows'][0][0] == 'Alpha'
        schema = client.get('/api/v1/resources/' + resource['id'] + '/schema', params={'access_option_id': option['access_option_id']})
        assert schema.is_success and any(c['table'] == 'items' for c in schema.json()['columns'])
    record = lab['admin'].get('/api/admin/v1/executions/' + result['id']).json()
    assert record['grant_id'] == lab['grants'][kind]['id'] and record['role_name'] == lab['role']['name']


def test_independent_mcp_role_discovery_and_execution(role_lab):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    async def run():
        async with streamablehttp_client(BASE + '/mcp/', headers={'X-DeeBee-API-Key': role_lab['keys']['normal']['key']}) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                found = await session.call_tool('resources.list', {})
                ssh = next(r for r in found.structuredContent['resources'] if r['type'] == 'ssh')
                result = await session.call_tool('ssh.exec', {'resource_id': ssh['id'], 'access_option_id': ssh['access_modes'][0]['access_option_id'], 'command': 'id -un', 'idempotency_key': uuid.uuid4().hex})
                assert not result.isError, result
                execution_id = result.structuredContent['execution_id']
                for _ in range(50):
                    result = await session.call_tool('executions.get', {'execution_id': execution_id})
                    if result.structuredContent['status'] == 'succeeded':
                        assert result.structuredContent['result']['stdout'].strip() == 'lab_reader'
                        return
                    await asyncio.sleep(.05)
                pytest.fail('Role MCP execution did not finish')
    asyncio.run(run())


def test_real_browser_shell_and_role_revocation(role_lab):
    from websockets.asyncio.client import connect
    lab = role_lab
    with httpx.Client(base_url=BASE, timeout=20) as person:
        login = person.post('/api/v1/local/login', json={'username': lab['principal']['username'], 'password': 'lab_local_test_password'})
        resource = person.get('/api/v1/resources/' + lab['resources']['ssh']['id']).json()
        ticket = person.post('/api/v1/sessions', headers={'x-csrf-token': login.json()['csrf']}, json={'resource_id': resource['id'], 'access_option_id': resource['access_modes'][0]['access_option_id']})
        assert ticket.status_code == 201, ticket.text
        async def run():
            url = BASE.replace('http:', 'ws:') + '/api/v1/sessions/' + ticket.json()['id'] + '/ssh'
            async with connect(url, origin=BASE, additional_headers={'Cookie': 'deebee_access=' + person.cookies.get('deebee_access')}) as ws:
                for _ in range(20):
                    message = json.loads(await asyncio.wait_for(ws.recv(), 5))
                    if message.get('state') == 'error':
                        pytest.fail(message.get('message', str(message)))
                    if message.get('state') == 'connected':
                        break
                else:
                    pytest.fail('SSH role session did not connect')
                await ws.send(json.dumps({'type': 'data', 'data': 'id -un\r'}))
                output = ''
                for _ in range(20):
                    message = json.loads(await asyncio.wait_for(ws.recv(), 5))
                    output += message.get('data', '')
                    if 'lab_reader' in output:
                        break
                assert 'lab_reader' in output
                role = next(r for r in lab['admin'].get('/api/admin/v1/roles').json()['items'] if r['id'] == lab['role']['id'])
                response = lab['admin'].patch('/api/admin/v1/roles/' + role['id'], json={'expected_version': role['version'], 'enabled': False})
                assert response.is_success
                messages = []
                for _ in range(10):
                    try:
                        messages.append(await asyncio.wait_for(ws.recv(), 3))
                    except Exception:
                        break
                assert ws.close_code is not None or any('失效' in m or '无访问权限' in m for m in messages)
                assert person.get('/api/v1/resources').json()['resources'] == []
                assert lab['admin'].patch('/api/admin/v1/roles/' + role['id'], json={'expected_version': response.json()['version'], 'enabled': True}).is_success
        asyncio.run(run())


def test_real_running_operation_revocation_and_historical_attribution(role_lab):
    lab = role_lab
    client, resource = lab['clients']['normal'], lab['resources']['ssh']
    option = client.get('/api/v1/resources/' + resource['id']).json()['access_modes'][0]
    response = client.post('/api/v1/ssh/executions', json={'resource_id': resource['id'], 'access_option_id': option['access_option_id'], 'command': 'sleep 20; id -un', 'idempotency_key': uuid.uuid4().hex})
    assert response.status_code == 202, response.text
    execution_id = response.json()['id']
    for _ in range(30):
        if lab['admin'].get('/api/admin/v1/executions/' + execution_id).json()['status'] == 'running':
            break
        time.sleep(.05)
    member = next(m for m in lab['admin'].get('/api/admin/v1/role-memberships').json()['items'] if m['id'] == lab['member']['id'])
    assert lab['admin'].patch('/api/admin/v1/role-memberships/' + member['id'], json={'expected_version': member['version'], 'enabled': False}).is_success
    for _ in range(50):
        record = lab['admin'].get('/api/admin/v1/executions/' + execution_id).json()
        if record['status'] in {'unknown', 'cancelled', 'failed'}:
            break
        time.sleep(.05)
    assert record['status'] in {'unknown', 'cancelled', 'failed'}, record
    assert record['role_name'] == lab['role']['name'] and record['grant_id'] == option['grant_id']
    assert client.get('/api/v1/resources').json()['resources'] == []
    role = next(r for r in lab['admin'].get('/api/admin/v1/roles').json()['items'] if r['id'] == lab['role']['id'])
    assert lab['admin'].request('DELETE', '/api/admin/v1/roles/' + role['id'], json={'expected_version': role['version'], 'confirm_name': role['name']}).is_success
    assert lab['admin'].get('/api/admin/v1/executions/' + execution_id).json()['role_name'] == role['name']
