from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from fastapi.testclient import TestClient

from deebee.main import api_app, current_user, workbench


def payload() -> dict[str, Any]:
    names = ['id', 'name', 'nullable', 'flag', 'binary', 'document', 'bigint', 'long_payload']
    values = [7, '中文 😀', None, False, {'$binary': '00ff', 'size': 2}, {'a': [1, True, None]}, '9223372036854775807', '完整正文😀' * 2000]
    return {'ok': True, 'elapsed_ms': 300, 'autocommit': True, 'warnings': [{'message': 'QA warning'}], 'status': {'connection_id': 9},
            'results': [{'index': 1, 'kind': 'rows', 'columns': [{'name': name, 'type': 'TEXT', 'nullable': True} for name in names],
                         'rows': [dict(zip(names, values)) for _ in range(130)], 'row_count': 130, 'truncated': False},
                        {'index': 2, 'kind': 'mutation', 'affected_rows': 3, 'last_insert_id': 19},
                        {'index': 3, 'kind': 'rows', 'columns': [{'name': 'empty'}], 'rows': [], 'row_count': 0}]}


@pytest.mark.parametrize('row_format', [None, 'array', 'unsupported'])
def test_query_array_is_opt_in_and_preserves_all_values(monkeypatch: pytest.MonkeyPatch, row_format: str | None) -> None:
    original = payload()
    calls = []
    def execute(*args: Any) -> dict[str, Any]:
        calls.append(args)
        return deepcopy(original)
    monkeypatch.setitem(api_app.dependency_overrides, current_user, lambda: 'qa')
    monkeypatch.setattr(workbench, 'execute', execute)
    headers = {'X-Deebee-Row-Format': row_format} if row_format else {}
    response = TestClient(api_app).post('/api/sessions/qa/query', json={'sql': 'QA query', 'limit': 10000}, headers=headers)
    assert response.status_code == 200 and response.headers['content-type'].startswith('application/json')
    result = response.json()
    if row_format == 'array':
        rows = result['results'][0]
        names = [column['name'] for column in rows['columns']]
        assert len(rows['rows']) == 130 and all(isinstance(row, list) for row in rows['rows'])
        assert rows['rows'][0][-1] == original['results'][0]['rows'][0]['long_payload']
        assert response.text.count('"long_payload"') == 1
        for item in result['results']:
            if item['kind'] == 'rows':
                assert item.pop('row_format') == 'array'
                columns = [column['name'] for column in item['columns']]
                item['rows'] = [dict(zip(columns, row)) for row in item['rows']]
        assert names == list(original['results'][0]['rows'][0])
    assert result == original
    assert calls == [('qa', 'QA query', 10000)]
