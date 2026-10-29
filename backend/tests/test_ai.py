import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from deebee.access.api import install_access
from deebee.access.models import AccessError
from deebee.ai import CompletionBody
from deebee.config import settings
from deebee.security import issue_token
from test_access_control import identity, principal, published, save


@pytest.fixture
def app(tmp_path):
    app = FastAPI()
    install_access(app, tmp_path)
    yield app
    app.state.access.store.close()


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        yield client


@pytest.fixture
def provider(monkeypatch):
    requests = []
    response = {"choices": [{"message": {"content": "* FROM customers"}}]}
    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=response)
    original = httpx.AsyncClient
    monkeypatch.setattr('deebee.ai.httpx.AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    return requests, response


ADMIN = {"Authorization": "Bearer " + issue_token(settings.admin_user)}
CONFIG = {"enabled": True, "base_url": "https://model.example/v1", "model": "test-model", "api_key": "test-secret-key"}


def test_config_admin_only_encrypted_and_key_lifecycle(app, client):
    assert client.get('/api/admin/v1/ai/settings').status_code == 401
    assert client.post('/api/ai/completions', json={"prefix": "SELECT "}).status_code == 401
    result = client.put('/api/admin/v1/ai/settings', headers=ADMIN, json=CONFIG)
    assert result.status_code == 200
    assert result.json()['has_api_key'] is True
    assert 'api_key' not in result.json()
    stored = app.state.access.store.db.execute("SELECT ciphertext FROM credentials WHERE id='ai-settings'").fetchone()[0]
    assert CONFIG['api_key'] not in stored
    assert client.put('/api/admin/v1/ai/settings', headers=ADMIN, json=CONFIG | {'api_key': '', 'model': 'other'}).status_code == 200
    assert app.state.ai.config()['api_key'] == CONFIG['api_key']
    # Existing credentials cannot be silently forwarded to a different gateway.
    assert client.put('/api/admin/v1/ai/settings', headers=ADMIN, json=CONFIG | {'api_key': '', 'base_url': 'https://other.example/v1'}).status_code == 400
    assert client.put('/api/admin/v1/ai/settings', headers=ADMIN, json=CONFIG | {'api_key': '', 'clear_api_key': True}).json()['has_api_key'] is False


@pytest.mark.parametrize('url', ['http://model.example/v1', 'https://user:pass@model.example/v1', 'https://model.example/v1?key=secret', 'https://model.example/v1/chat/completions'])
def test_invalid_config_does_not_echo_credentials(client, url):
    result = client.put('/api/admin/v1/ai/settings', headers=ADMIN, json=CONFIG | {'base_url': url, 'api_key': 'private\nkey'})
    assert result.status_code == 400
    assert 'private' not in result.text
    assert 'user:pass' not in result.text


def test_disabled_does_not_call_provider_and_status_hides_config(client, provider):
    assert client.get('/api/ai/status', headers=ADMIN).json() == {'enabled': False, 'share_schema': False}
    assert client.post('/api/ai/completions', headers=ADMIN, json={'prefix': 'SELECT '}).json()['reason'] == 'disabled'
    assert provider[0] == []


def test_provider_context_and_audit_privacy(app, client, provider):
    client.put('/api/admin/v1/ai/settings', headers=ADMIN, json=CONFIG)
    body = {'prefix': 'SELECT ', 'suffix': ';', 'schema_context': 'customers(email)', 'language': 'postgresql'}
    result = client.post('/api/ai/completions', headers=ADMIN, json=body)
    assert result.json()['completion'] == '* FROM customers'
    request = provider[0][0]
    assert str(request.url) == 'https://model.example/v1/chat/completions'
    assert request.headers['authorization'] == 'Bearer test-secret-key'
    prompt = json.loads(json.loads(request.content)['messages'][1]['content'])
    assert prompt == {'prefix': 'SELECT ', 'suffix': ';', 'language': 'postgresql'}
    client.put('/api/admin/v1/ai/settings', headers=ADMIN, json=CONFIG | {'share_schema': True})
    client.post('/api/ai/completions', headers=ADMIN, json=body)
    assert 'customers(email)' in provider[0][-1].content.decode()
    for event in app.state.access.store.audits():
        text = json.dumps(event)
        assert 'test-secret-key' not in text
        assert 'customers' not in text
        assert 'SELECT' not in text


def test_sensitive_input_and_output_are_suppressed(client, provider):
    client.put('/api/admin/v1/ai/settings', headers=ADMIN, json=CONFIG)
    assert client.post('/api/ai/completions', headers=ADMIN, json={'prefix': 'curl --token secret'}).json()['reason'] == 'sensitive'
    assert provider[0] == []
    provider[1]['choices'][0]['message']['content'] = '```sql\nSELECT 1;\n```'
    assert client.post('/api/ai/completions', headers=ADMIN, json={'prefix': 'SELECT '}).json()['completion'] == ''


def test_test_connection_uses_unsaved_settings_without_enabling(app, client, provider):
    assert client.post('/api/admin/v1/ai/test', headers=ADMIN, json=CONFIG | {'enabled': False}).json()['ok'] is True
    assert len(provider[0]) == 1
    assert app.state.ai.config()['enabled'] is False
    assert app.state.ai.config()['api_key'] == ''


def test_qwen37_coding_plan_uses_non_thinking_completion(client, provider):
    client.put('/api/admin/v1/ai/settings', headers=ADMIN, json=CONFIG | {
        'base_url': 'https://coding.dashscope.aliyuncs.com/v1', 'model': 'qwen3.7-plus',
    })
    assert client.post('/api/ai/completions', headers=ADMIN, json={'prefix': 'SELECT '}).status_code == 200
    assert json.loads(provider[0][-1].content)['enable_thinking'] is False
    client.put('/api/admin/v1/ai/settings', headers=ADMIN, json=CONFIG)
    client.post('/api/ai/completions', headers=ADMIN, json={'prefix': 'SELECT '})
    assert 'enable_thinking' not in json.loads(provider[0][-1].content)


def test_user_auth_resource_boundary_and_no_admin_config(app, client, provider):
    service = app.state.access
    p = principal(service)
    resource, accounts = published(service)
    ctx, key = identity(service, p['id'])
    headers = {'X-DeeBee-API-Key': key}
    client.put('/api/admin/v1/ai/settings', headers=ADMIN, json=CONFIG)
    assert client.get('/api/admin/v1/ai/settings', headers=headers).status_code == 403
    body = {'prefix': 'SELECT ', 'resource_id': resource['id']}
    assert client.post('/api/v1/ai/completions', headers=headers, json=body).status_code == 404
    save(service, 'grants', {'principal_id': p['id'], 'resource_id': resource['id'], 'normal_account_id': accounts[0]})
    assert client.post('/api/v1/ai/completions', headers=headers, json=body).status_code == 200
    assert client.post('/api/v1/ai/completions', headers=headers, json=body | {'language': 'shell'}).status_code == 403
    assert len(provider[0]) == 1


def test_user_cookie_requires_csrf(app, client, provider):
    service = app.state.access
    p = save(service, 'principals', {'name': 'User', 'kind': 'human', 'username': 'aiuser', 'password': 'test-password-long'})
    token, csrf = service.auth.login('aiuser', 'test-password-long', 'local')
    client.cookies.set('deebee_access', token)
    assert client.get('/api/v1/ai/status').status_code == 200
    assert client.post('/api/v1/ai/completions', json={'prefix': 'SELECT '}).status_code == 403
    assert provider[0] == []


def test_rate_limit_and_input_bounds(client, provider):
    client.put('/api/admin/v1/ai/settings', headers=ADMIN, json=CONFIG)
    assert client.post('/api/ai/completions', headers=ADMIN, json={'prefix': 'x' * 12001}).status_code == 400
    for _ in range(29):
        client.post('/api/ai/completions', headers=ADMIN, json={'prefix': 'SELECT '})
    assert client.post('/api/ai/completions', headers=ADMIN, json={'prefix': 'SELECT '}).status_code == 429


def test_oversized_body_is_rejected_before_parsing(client, provider):
    result = client.post('/api/ai/completions', headers=ADMIN, json={'prefix': 'x' * 270000})
    assert result.status_code == 413
    assert provider[0] == []


@pytest.mark.parametrize('mode', ['timeout', 'redirect', 'malformed', 'large'])
def test_provider_failures_are_bounded_and_sanitized(app, monkeypatch, mode):
    app.state.ai.save(CONFIG, 'admin')
    def handle(request):
        if mode == 'timeout': raise httpx.ReadTimeout('test-secret-key', request=request)
        if mode == 'redirect': return httpx.Response(302, headers={'location': 'https://other.example'}, text='test-secret-key')
        if mode == 'large': return httpx.Response(200, content=b'x' * 65537)
        return httpx.Response(200, json={'private': 'test-secret-key'})
    original = httpx.AsyncClient
    monkeypatch.setattr('deebee.ai.httpx.AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    with pytest.raises(AccessError) as error:
        asyncio.run(app.state.ai.complete(CompletionBody(prefix='SELECT ')))
    assert error.value.status in (502, 504)
    assert 'test-secret-key' not in str(error.value)


@pytest.mark.parametrize('language,code', [
    ('mysql', 'SELECT id FROM users ORDER BY id DESC LIMIT 10;'),
    ('postgresql', 'SELECT id FROM users ORDER BY id DESC LIMIT 10;'),
    ('mssql', 'SELECT TOP 10 id FROM users ORDER BY id DESC;'),
    ('shell', 'find . -type f -size +100M'),
])
def test_natural_language_generation_uses_dialect_and_never_executes(app, client, provider, language, code):
    client.put('/api/admin/v1/ai/settings', headers=ADMIN, json=CONFIG)
    provider[1]['choices'][0]['message']['content'] = json.dumps({'code': code, 'message': ''})
    result = client.post('/api/ai/generate', headers=ADMIN, json={
        'instruction': '查询 users 表最新的 10 个 id', 'language': language, 'schema_context': 'private_table(email)',
    })
    assert result.status_code == 200
    assert result.json() == {'code': code, 'message': ''}
    payload = json.loads(provider[0][-1].content)
    assert payload['max_tokens'] == 1200
    assert json.loads(payload['messages'][1]['content']) == {'instruction': '查询 users 表最新的 10 个 id', 'language': language}
    assert app.state.access.store.db.execute('SELECT COUNT(*) FROM executions').fetchone()[0] == 0
    for event in app.state.access.store.audits():
        assert code not in json.dumps(event)
        assert 'private_table' not in json.dumps(event)


def test_generation_auth_disabled_and_sensitive_inputs(client, provider):
    body = {'instruction': '列出当前目录的大文件', 'language': 'shell'}
    assert client.post('/api/ai/generate', json=body).status_code == 401
    assert client.post('/api/ai/generate', headers=ADMIN, json=body).status_code == 409
    client.put('/api/admin/v1/ai/settings', headers=ADMIN, json=CONFIG)
    for instruction in ['', '   ', 'x' * 4001, '连接 --password secret']:
        assert client.post('/api/ai/generate', headers=ADMIN, json=body | {'instruction': instruction}).status_code == 400
    assert provider[0] == []


def test_generation_can_request_clarification_and_opt_in_schema(client, provider):
    client.put('/api/admin/v1/ai/settings', headers=ADMIN, json=CONFIG | {'share_schema': True})
    provider[1]['choices'][0]['message']['content'] = json.dumps({'code': '', 'message': '请指定要查询的表名。'})
    result = client.post('/api/ai/generate', headers=ADMIN, json={'instruction': '查询用户', 'schema_context': 'users(id)'})
    assert result.json() == {'code': '', 'message': '请指定要查询的表名。'}
    assert json.loads(json.loads(provider[0][-1].content)['messages'][1]['content'])['schema'] == 'users(id)'


@pytest.mark.parametrize('output,finish', [
    ('SELECT * FROM users;', 'stop'),
    ('{"code":42}', 'stop'),
    ('{"code":"SELECT 1;"}', 'length'),
    ('{"code":"```sql SELECT 1;```"}', 'stop'),
    ('{"code":"curl --token secret"}', 'stop'),
    ('{"code":"echo \\u001b"}', 'stop'),
])
def test_generation_rejects_invalid_or_truncated_output(client, provider, output, finish):
    client.put('/api/admin/v1/ai/settings', headers=ADMIN, json=CONFIG)
    provider[1]['choices'][0] = {'message': {'content': output}, 'finish_reason': finish}
    assert client.post('/api/ai/generate', headers=ADMIN, json={'instruction': '查询 users 表'}).status_code == 502


def test_user_generation_respects_resource_auth_and_csrf(app, client, provider):
    service = app.state.access
    p = principal(service)
    resource, accounts = published(service)
    _, key = identity(service, p['id'])
    headers = {'X-DeeBee-API-Key': key}
    client.put('/api/admin/v1/ai/settings', headers=ADMIN, json=CONFIG)
    provider[1]['choices'][0]['message']['content'] = '{"code":"SELECT 1;"}'
    body = {'instruction': '查询常量 1', 'resource_id': resource['id']}
    assert client.post('/api/v1/ai/generate', headers=headers, json=body).status_code == 404
    save(service, 'grants', {'principal_id': p['id'], 'resource_id': resource['id'], 'normal_account_id': accounts[0]})
    assert client.post('/api/v1/ai/generate', headers=headers, json=body).json()['code'] == 'SELECT 1;'
    assert client.post('/api/v1/ai/generate', headers=headers, json=body | {'language': 'shell'}).status_code == 403
    save(service, 'principals', {'name': 'Human', 'kind': 'human', 'username': 'genuser', 'password': 'test-password-long'})
    token, _ = service.auth.login('genuser', 'test-password-long', 'local')
    client.cookies.set('deebee_access', token)
    assert client.post('/api/v1/ai/generate', json=body).status_code == 403
    assert len(provider[0]) == 1
