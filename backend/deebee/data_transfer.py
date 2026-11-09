from __future__ import annotations

import csv
import io
import json
import re
from typing import Any
from zipfile import BadZipFile

from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.utils.exceptions import InvalidFileException
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

from .mysql import DeeBeeError
from .data_values import is_generated_column


def text_value(value: Any, null_marker: str = r'\N') -> str:
    if value is None:
        return null_marker
    if isinstance(value, dict) and isinstance(value.get('$binary'), str):
        return '0x' + value['$binary']
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return '1' if value else '0'
    text = str(value)
    return '\\' + text if text == null_marker or text.startswith('\\\\') else text


def parse_rows(filename: str, content: bytes, *, delimiter: str = ',', encoding: str = 'utf-8-sig', null_marker: str = r'\N') -> list[dict[str, Any]]:
    suffix = filename.lower().rsplit('.', 1)[-1]
    if len(content) > 50 * 1024 * 1024:
        raise DeeBeeError('数据文件不能超过 50 MB；大型 SQL 文件请使用 SQL 导入')
    if suffix == 'csv':
        if delimiter not in {',', ';', '\t', '|'} or encoding not in {'utf-8-sig', 'utf-8', 'gb18030'}:
            raise DeeBeeError('CSV 分隔符或编码无效')
        try:
            reader = csv.DictReader(io.StringIO(content.decode(encoding)), delimiter=delimiter, strict=True)
            headers = reader.fieldnames or []
            if not headers or any(not name for name in headers) or len(set(headers)) != len(headers):
                raise DeeBeeError('CSV 表头不能为空或重复')
            rows = []
            for number, row in enumerate(reader, 2):
                if None in row or any(value is None for value in row.values()):
                    raise DeeBeeError(f'CSV 第 {number} 行的字段数量与表头不同')
                rows.append({key: None if null_marker and value == null_marker else value[1:] if value.startswith('\\\\') else value for key, value in row.items()})
                if len(rows) > 100000:
                    raise DeeBeeError('单次最多导入 100000 行')
            return rows
        except (UnicodeError, csv.Error) as exc:
            raise DeeBeeError('CSV 编码或格式无效，请检查编码、分隔符和引号') from exc
    if suffix == 'json':
        try:
            parsed = json.loads(content)
        except (ValueError, UnicodeError) as exc:
            raise DeeBeeError('JSON 文件格式无效') from exc
        if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
            raise DeeBeeError('JSON 文件必须是对象数组')
        return parsed
    if suffix in {'xlsx', 'xlsm'}:
        try:
            workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=False)
        except (BadZipFile, InvalidFileException, OSError, KeyError, ValueError) as exc:
            raise DeeBeeError('Excel 文件格式无效，请选择有效的 XLSX 文件') from exc
        try:
            sheet = workbook.active
            cells = iter(sheet.iter_rows())
            first = next(cells, None)
            if not first:
                raise DeeBeeError('Excel 文件没有数据')
            headers = [str(cell.value) if cell.value is not None else '' for cell in first]
            if any(not name for name in headers) or len(set(headers)) != len(headers):
                raise DeeBeeError('Excel 表头不能为空或重复')
            rows = []
            for row in cells:
                if any(cell.data_type == 'f' for cell in row):
                    raise DeeBeeError('Excel 导入不执行公式，请先将公式转换为值')
                rows.append(dict(zip(headers, ('' if cell.value is None and cell.data_type == 'inlineStr' else cell.value for cell in row))))
                if len(rows) > 100000:
                    raise DeeBeeError('单次最多导入 100000 行')
            return rows
        finally:
            workbook.close()
    raise DeeBeeError('仅支持 CSV、JSON 和 XLSX 文件')


def mapped_rows(rows: list[dict[str, Any]], mapping: str, target_columns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        raise DeeBeeError('导入文件没有数据')
    allowed = {column['name'] for column in target_columns if not is_generated_column(column)}
    if mapping:
        try:
            fields = json.loads(mapping)
        except ValueError as exc:
            raise DeeBeeError('字段映射格式无效') from exc
        if not isinstance(fields, dict) or any(source not in rows[0] or not isinstance(target, str) or target and target not in allowed for source, target in fields.items()):
            raise DeeBeeError('字段映射包含未知字段')
        targets = [target for target in fields.values() if target]
        if len(targets) != len(set(targets)):
            raise DeeBeeError('多个源字段不能映射到同一目标字段')
    else:
        fields = {name: name for name in rows[0] if name in allowed}
    if not any(fields.values()):
        raise DeeBeeError('请至少映射一个可写字段')
    return [{target: row.get(source) for source, target in fields.items() if target} for row in rows]


def csv_bytes(columns: list[str], rows: list[dict[str, Any]], *, delimiter: str = ',', header: bool = True, null_marker: str = r'\N') -> bytes:
    if delimiter not in {',', ';', '\t', '|'}:
        raise DeeBeeError('CSV 分隔符无效')
    output = io.StringIO(newline='')
    writer = csv.writer(output, delimiter=delimiter)
    if header:
        writer.writerow(columns)
    writer.writerows([[text_value(row.get(name), null_marker) for name in columns] for row in rows])
    return ('\ufeff' + output.getvalue()).encode('utf-8')


def xlsx_bytes(title: str, columns: list[str], rows: list[dict[str, Any]]) -> bytes:
    if len(rows) > 1048575 or len(columns) > 16384:
        raise DeeBeeError('数据超过 Excel 工作表限制，请使用 CSV 或 SQL')
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = re.sub(r'[\\/*?:\[\]]', '_', title)[:31] or 'Data'
    sheet.freeze_panes = 'A2'
    sheet.append(columns)
    for cell in sheet[1]:
        cell.data_type = 's'
    for index, row in enumerate(rows, 2):
        for col, name in enumerate(columns, 1):
            value = row.get(name)
            if isinstance(value, (dict, list)):
                value = text_value(value)
            if isinstance(value, str) and ILLEGAL_CHARACTERS_RE.search(value):
                raise DeeBeeError(f'第 {index} 行 {name} 包含 Excel 不支持的控制字符，请使用 CSV 或 SQL')
            if isinstance(value, str) and len(value) > 32767:
                raise DeeBeeError(f'第 {index} 行 {name} 超过 Excel 单元格字符限制，请使用 CSV 或 SQL')
            cell = sheet.cell(index, col, value)
            if isinstance(value, str):
                # Preserve strings, exact numbers and formula-looking user data.
                cell.data_type = 's'
    sheet.auto_filter.ref = sheet.dimensions
    for index, name in enumerate(columns, 1):
        sheet.column_dimensions[get_column_letter(index)].width = min(40, max(14, len(name) + 4))
    output = io.BytesIO();workbook.save(output);workbook.close()
    return output.getvalue()
