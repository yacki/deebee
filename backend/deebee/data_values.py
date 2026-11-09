from __future__ import annotations

import datetime as dt
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from .mysql import DeeBeeError


def is_generated_column(column: dict[str, Any]) -> bool:
    return bool(re.search(r'\b(?:VIRTUAL|STORED)\s+GENERATED\b|^GENERATED\b', str(column.get('extra', '')), re.IGNORECASE))


def mysql_value(value: Any, column: dict[str, Any]) -> Any:
    """Convert the public value representation into a bound MySQL parameter."""
    if value is None:
        if not column.get('nullable', True):
            raise DeeBeeError(f"{column['name']} 不允许 NULL")
        return None
    kind = str(column.get('data_type', '')).lower()
    name = column['name']
    if kind.startswith('json'):
        try:
            if isinstance(value, str):
                def invalid_constant(token):
                    raise ValueError(token)
                json.loads(value, parse_float=Decimal, parse_constant=invalid_constant)
                return value
            return json.dumps(value, ensure_ascii=False, allow_nan=False)
        except (ValueError, TypeError) as exc:
            raise DeeBeeError(f'{name} 的 JSON 格式无效') from exc
    if isinstance(value, dict) and isinstance(value.get('$binary'), str):
        try:
            raw = bytes.fromhex(value['$binary'])
        except ValueError as exc:
            raise DeeBeeError(f'{name} 的十六进制数据无效') from exc
        return int.from_bytes(raw, 'big') if kind.startswith('bit') else raw
    if re.match(r'^(?:tiny|medium|long)?blob|^(?:var)?binary', kind):
        if isinstance(value, str) and value.lower().startswith('0x'):
            try:
                return bytes.fromhex(value[2:])
            except ValueError as exc:
                raise DeeBeeError(f'{name} 的十六进制数据无效') from exc
        if isinstance(value, (bytes, bytearray)):
            return bytes(value)
        raise DeeBeeError(f'{name} 请使用 0x 开头的十六进制数据')
    if re.match(r'^(tinyint|smallint|mediumint|int|integer|bigint|bit|year|bool)', kind):
        text = str(int(value)) if isinstance(value, bool) else str(value).strip()
        if text.lower() in {'true', 'false'}:
            text = '1' if text.lower() == 'true' else '0'
        if kind.startswith('bit') and re.fullmatch(r'0x[0-9a-f]+', text, re.IGNORECASE):
            return int(text[2:], 16)
        if not re.fullmatch(r'[+-]?\d+', text):
            raise DeeBeeError(f'{name} 必须是整数')
        return int(text)
    if re.match(r'^(decimal|numeric|float|double|real)', kind):
        try:
            parsed = Decimal(str(value))
            if not parsed.is_finite():
                raise InvalidOperation
            return parsed
        except (ValueError, InvalidOperation) as exc:
            raise DeeBeeError(f'{name} 必须是有效数字') from exc
    if kind.startswith('set(') and isinstance(value, list):
        return ','.join(str(item) for item in value)
    if isinstance(value, (dict, list)):
        raise DeeBeeError(f'{name} 需要文本值')
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat(sep=' ') if isinstance(value, dt.datetime) else value.isoformat()
    return value
