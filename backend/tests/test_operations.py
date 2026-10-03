"""Maintenance boundary regressions. Real drivers covered by operations live lab."""
import asyncio

import pytest
from pydantic import ValidationError

from deebee.access.models import AccessError, Resource
from deebee.access.operations import KubernetesInput, SSHInspectInput, namespace_for, kubernetes_execute
from deebee.access.drivers import RunningHandle
from deebee.access.executions import ExecutionManager
from test_access_control import service, save, principal, published, identity


def test_metadata_is_projected_for_disambiguation(service):
    p = principal(service)
    r, accounts = published(service, 'ssh')
    save(service, 'resources', {'expected_version': r['version'], 'environment': 'production', 'project_groups': ['payments'], 'tags': ['west']}, r['id'])
    save(service, 'grants', {'principal_id': p['id'], 'resource_id': r['id'], 'normal_account_id': accounts[0]})
    ctx, _ = identity(service, p['id'])
    item = service.resources(ctx)[0]
    assert (item['environment'], item['project_groups'], item['tags']) == ('production', ['payments'], ['west'])
    assert 'ssh.inspect' in item['access_modes'][0]['actions']
    assert not any(k in item for k in ('password', 'private_key', 'credential_ref'))


@pytest.mark.parametrize('body', [
    {'command': 'touch /tmp/should-not-exist'}, {'check': 'disk; touch /tmp/pwn'}, {'check': 'logs'},
])
def test_ssh_inspect_cannot_accept_shell(body):
    with pytest.raises(ValidationError):
        SSHInspectInput(resource_id='r', idempotency_key='a', **body)


@pytest.mark.parametrize('body', [
    {'kind': 'secrets'}, {'namespace': '../other'}, {'name': 'a/exec'}, {'operation': 'exec'},
    {'operation': 'restart', 'kind': 'pods', 'name': 'x'}, {'operation': 'logs'},
    {'operation': 'list', 'name': 'x'}, {'operation': 'get', 'name': 'x', 'previous': True},
])
def test_kubernetes_rejects_unbounded_paths_and_operations(body):
    with pytest.raises(ValidationError):
        KubernetesInput(resource_id='r', idempotency_key='a', **body)


def test_namespace_resolution_is_fail_closed():
    assert namespace_for({'namespaces': ['one']}, '') == 'one'
    for requested in ('', 'other'):
        with pytest.raises(AccessError, match='namespace'):
            namespace_for({'namespaces': ['one', 'two']}, requested)
    with pytest.raises(ValidationError):
        Resource(name='k8s', type='k8s', host='https://example.com', port=6443)


def test_read_tool_cannot_restart_and_restart_requires_privilege(service):
    # The shared fixture helpers use asyncio.run, so keep their setup synchronous.
    p = principal(service)
    r, accounts = published(service, 'k8s')
    save(service, 'grants', {'principal_id': p['id'], 'resource_id': r['id'], 'normal_account_id': accounts[0], 'privileged_account_id': accounts[1], 'allow_privileged': True})
    ctx, _ = identity(service, p['id'])
    manager = ExecutionManager(service)
    body = {'resource_id': r['id'], 'operation': 'restart', 'kind': 'deployments', 'name': 'web', 'idempotency_key': 'restart'}
    for tool, code in [('k8s.read', 'INVALID_ARGUMENT'), ('k8s.restart', 'PRIVILEGE_DENIED')]:
        with pytest.raises(AccessError) as denied:
            asyncio.run(manager.submit(ctx, tool, body))
        assert denied.value.code == code
    assert not manager.tasks


def test_kubernetes_results_do_not_expose_pod_spec(monkeypatch):
    async def request(*args, **kwargs):
        return {'metadata': {'name': 'web', 'namespace': 'ops', 'annotations': {'password': 'secret'}},
                'spec': {'containers': [{'env': [{'value': 'secret'}]}]}, 'status': {'phase': 'Running'}}
    monkeypatch.setattr('deebee.access.operations.kube_request', request)
    body = KubernetesInput(resource_id='r', idempotency_key='a', namespace='ops', operation='get', name='web').model_dump()
    result = asyncio.run(kubernetes_execute({'namespaces': ['ops']}, {}, {}, body, 4096, RunningHandle()))
    assert result['resource']['status']['phase'] == 'Running'
    assert 'secret' not in str(result)

@pytest.mark.parametrize('status,expected', [
    ({'resourceRules': [{'apiGroups': [''], 'resources': ['pods'], 'verbs': ['get', 'list']}]}, True),
    ({'resourceRules': [{'apiGroups': ['*'], 'resources': ['*'], 'verbs': ['*']}]}, False),
    ({'resourceRules': [], 'incomplete': True}, False),
    ({'evaluationError': 'RBAC unavailable'}, False),
])
def test_kubernetes_account_verification_fails_closed(monkeypatch, status, expected):
    from deebee.access.operations import inspect_kubernetes
    async def request(resource, secret, method, path, **kwargs):
        return {'gitVersion': 'test'} if path == '/version' else {'status': status}
    monkeypatch.setattr('deebee.access.operations.kube_request', request)
    result = asyncio.run(inspect_kubernetes({'namespaces': ['ops']}, {'username': 'reader'}, {}))
    assert result['connected'] and result['normal_safe'] is expected


def test_kubernetes_output_limit_and_redirect_protection(monkeypatch):
    import httpx
    from deebee.access.operations import kube_request
    real_client = httpx.AsyncClient
    responses = [httpx.Response(200, content=b'x' * 2048), httpx.Response(302, headers={'location': 'http://other/private'})]
    def client(**kwargs):
        assert kwargs['follow_redirects'] is False and kwargs['trust_env'] is False
        return real_client(transport=httpx.MockTransport(lambda request: responses.pop(0)))
    monkeypatch.setattr('deebee.access.operations.httpx.AsyncClient', client)
    resource = {'host': 'cluster', 'port': 6443, 'tls': True}
    for code in ('OUTPUT_LIMIT', 'K8S_REQUEST_FAILED'):
        with pytest.raises(AccessError) as error:
            asyncio.run(kube_request(resource, {'password': 'sensitive'}, 'GET', '/version', limit=1024))
        assert error.value.code == code
        assert 'sensitive' not in str(error.value)


def test_filtered_resources_keep_authorized_same_name_alternatives(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from deebee.access.api import install_access
    app = FastAPI()
    install_access(app, tmp_path)
    service = app.state.access
    p = principal(service)
    for kind, environment in [('mysql', 'production'), ('postgresql', 'staging')]:
        # PostgreSQL fixture needs a default database when enabled.
        r, accounts = published(service, 'mysql')
        raw = service.store.get('resources', r['id'])
        service.store.put('resources', raw['id'], raw | {'name': 'orders', 'type': kind, 'environment': environment}, raw['version'])
        fresh = service.store.get('resources', r['id'])
        for account_id in accounts:
            a = service.store.get('accounts', account_id)
            service.store.put('accounts', a['id'], a | {'test_result': a['test_result'] | {'resource_version': fresh['version']}}, a['version'])
        save(service, 'grants', {'principal_id': p['id'], 'resource_id': r['id'], 'normal_account_id': accounts[0]})
    _, key = identity(service, p['id'])
    with TestClient(app) as client:
        response = client.get('/api/v1/resources?type=mysql', headers={'X-DeeBee-API-Key': key})
        assert response.status_code == 200
        result = response.json()
        assert len(result['resources']) == 1 and result['resources'][0]['type'] == 'mysql'
        candidates = result['selection_context']['same_name_alternatives']
        assert len(candidates) == 1 and candidates[0]['type'] == 'postgresql' and candidates[0]['environment'] == 'staging'
        other = principal(service, 'No grants')
        _, other_key = identity(service, other['id'])
        result = client.get('/api/v1/resources?type=mysql', headers={'X-DeeBee-API-Key': other_key}).json()
        assert result['resources'] == [] and result['selection_context']['same_name_alternatives'] == []
    service.store.close()


def test_rollout_waits_for_new_generation_and_all_replicas(monkeypatch):
    calls = []
    async def request(*args, **kwargs):
        calls.append(args)
        return {'metadata': {'name': 'web', 'generation': 2}, 'spec': {'replicas': 1},
                'status': {'observedGeneration': 1 if len(calls) == 1 else 2, 'replicas': 1, 'updatedReplicas': 1, 'availableReplicas': 1}}
    monkeypatch.setattr('deebee.access.operations.kube_request', request)
    body = KubernetesInput(resource_id='r', idempotency_key='a', namespace='ops', operation='rollout', kind='deployments', name='web').model_dump()
    result = asyncio.run(kubernetes_execute({'namespaces': ['ops']}, {}, {}, body, 4096, RunningHandle()))
    assert len(calls) == 2
    assert result['resource']['status']['observedGeneration'] == result['resource']['generation'] == 2
    assert result['verification']['rollout_observed'] is True
    assert result['verification']['maintenance_verified'] is False


def test_restart_receipt_does_not_verify_old_ready_replicas(monkeypatch):
    async def request(*args, **kwargs):
        assert args[2] == 'PATCH'
        return {'metadata': {'name': 'web', 'generation': 3}, 'spec': {'replicas': 1},
                'status': {'observedGeneration': 2, 'replicas': 1, 'updatedReplicas': 1, 'availableReplicas': 1}}
    monkeypatch.setattr('deebee.access.operations.kube_request', request)
    body = KubernetesInput(resource_id='r', idempotency_key='a', namespace='ops', operation='restart', kind='deployments', name='web').model_dump()
    result = asyncio.run(kubernetes_execute({'namespaces': ['ops']}, {}, {}, body, 4096, RunningHandle()))
    assert result['resource']['status']['availableReplicas'] == 1
    verification = result['verification']
    assert verification['target_generation'] == 3
    assert verification['rollout_observed'] is verification['pods_observed'] is verification['maintenance_verified'] is False
    assert len(verification['remaining_checks']) == 2


def test_pod_ownership_is_available_without_annotations_or_spec(monkeypatch):
    async def request(*args, **kwargs):
        return {'items': [{'metadata': {'name': 'web-new', 'creationTimestamp': '2026-09-24T08:00:00Z',
                    'ownerReferences': [{'kind': 'ReplicaSet', 'name': 'web-rs', 'uid': 'rs-uid', 'controller': True}],
                    'annotations': {'secret': 'private'}}, 'spec': {'containers': []}, 'status': {'phase': 'Running'}}]}
    monkeypatch.setattr('deebee.access.operations.kube_request', request)
    body = KubernetesInput(resource_id='r', idempotency_key='a', namespace='ops').model_dump()
    result = asyncio.run(kubernetes_execute({'namespaces': ['ops'], 'max_rows': 100}, {}, {}, body, 4096, RunningHandle()))
    assert result['items'][0]['owners'] == [{'kind': 'ReplicaSet', 'name': 'web-rs', 'uid': 'rs-uid'}]
    assert result['items'][0]['created_at'] == '2026-09-24T08:00:00Z'
    assert 'private' not in str(result) and 'containers' not in str(result)
