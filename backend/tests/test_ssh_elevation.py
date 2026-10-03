import asyncio
import shlex
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from deebee.access.drivers import elevated_ssh_command, ssh_execute, RunningHandle
from deebee.access.models import AccessError
from deebee.access.executions import ExecutionManager
from test_access_control import service, save, principal, published, identity


def test_sudo_is_explicit_and_does_not_put_credentials_in_command():
    command = "printf '%s' \"quoted value\"\ntrue"
    assert elevated_ssh_command({}, {}, command, 'none') == (command, '')
    with pytest.raises(AccessError):
        elevated_ssh_command({'tier': 'normal'}, {}, command, 'sudo')
    wrapped, password = elevated_ssh_command({'tier': 'privileged'}, {'sudo_password': 'private-fixture'}, command, 'sudo')
    assert password == 'private-fixture' and password not in wrapped
    assert shlex.split(wrapped)[-1] == 'exec </dev/null\n' + command
    assert elevated_ssh_command({'tier': 'privileged'}, {}, command, 'sudo')[0].startswith('sudo -n ')
    with pytest.raises(AccessError):
        elevated_ssh_command({'tier': 'privileged'}, {'sudo_password': 'invalid\ncredential'}, command, 'sudo')


def test_sudo_secret_is_hidden_and_encrypted(service):
    resource, _ = published(service, 'ssh')
    account = save(service, 'accounts', {'name': 'sudo', 'resource_id': resource['id'], 'username': 'operator',
                                       'tier': 'privileged', 'password': 'login-secret', 'sudo_password': 'sudo-secret'})
    assert 'sudo_password' not in account
    raw = service.store.get('accounts', account['id'])
    assert service.account_secret(raw)['sudo_password'] == 'sudo-secret'
    assert 'sudo-secret' not in '\n'.join(service.store.db.iterdump())


def test_normal_mode_cannot_request_elevation(service):
    p = principal(service); resource, accounts = published(service, 'ssh')
    save(service, 'grants', {'principal_id': p['id'], 'resource_id': resource['id'], 'normal_account_id': accounts[0]})
    ctx, _ = identity(service, p['id']); manager = ExecutionManager(service)
    with pytest.raises(AccessError) as denied:
        asyncio.run(manager.submit(ctx, 'ssh.exec', {'resource_id': resource['id'], 'command': 'id',
                                                  'elevation': 'sudo', 'idempotency_key': 'denied'}))
    assert denied.value.code == 'PRIVILEGE_DENIED'
    assert not manager.tasks


def test_driver_uses_stdin_and_redacts_output(monkeypatch):
    stdin = Mock()
    process = SimpleNamespace(stdin=stdin, stdout=SimpleNamespace(read=AsyncMock(side_effect=['private-fixture result', ''])),
                              stderr=SimpleNamespace(read=AsyncMock(return_value='')), wait_closed=AsyncMock(), exit_status=0)
    connection = SimpleNamespace(create_process=AsyncMock(return_value=process), close=Mock(), wait_closed=AsyncMock())
    monkeypatch.setattr('deebee.access.drivers.ssh_connect', AsyncMock(return_value=connection))
    result = asyncio.run(ssh_execute({}, {'tier': 'privileged'}, {'sudo_password': 'private-fixture'}, 'id', 4096,
                                     RunningHandle(), elevation='sudo'))
    stdin.write.assert_called_once_with('private-fixture\n'); stdin.write_eof.assert_called_once()
    assert 'private-fixture' not in connection.create_process.call_args.args[0]
    assert result['stdout'] == '[REDACTED] result' and result['exit_code'] == 0
    connection.close.assert_called_once()
