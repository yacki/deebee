from __future__ import annotations

import datetime as dt
import io
import json
import threading
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pymysql
import pytest
from openpyxl import load_workbook

from deebee.data_transfer import csv_bytes, mapped_rows, parse_rows, xlsx_bytes
from deebee.data_values import mysql_value
from deebee.mysql import DbSession, DeeBeeError, json_value
from deebee.workbench import DatabaseWorkbenches


def column(kind, nullable=True):
    return {'name':'value','data_type':kind,'nullable':nullable}


@pytest.mark.parametrize(('kind','value','expected'),[
 ('bigint unsigned','18446744073709551615',18446744073709551615),
 ('decimal(30,10)','12345678901234567890.1234567890',Decimal('12345678901234567890.1234567890')),
 ('float','1.25',Decimal('1.25')),('tinyint(1)','false',0),('bit(8)','0xff',255),
 ('json','null','null'),('json',{'中文':[1,True]},'{"中文": [1, true]}'),
 ('blob',{'$binary':'00ff','size':2},b'\x00\xff'),('varbinary(20)','0x41ff',b'A\xff'),
 ('varchar(20)','',''),('varchar(20)',None,None),('set(\'a\',\'b\')',['a','b'],'a,b'),
 ('date',dt.date(2026,11,8),'2026-11-08'),('datetime(6)',dt.datetime(2026,11,8,12,34,56,123456),'2026-11-08 12:34:56.123456'),
])
def test_value_conversion_preserves_database_types(kind,value,expected):
    assert mysql_value(value,column(kind))==expected


@pytest.mark.parametrize(('kind','value'),[('json','{bad'),('bigint','1.2'),('decimal(10,2)','NaN'),('blob','0xzz')])
def test_invalid_values_fail_before_writing(kind,value):
    with pytest.raises(DeeBeeError):mysql_value(value,column(kind))


def test_wire_numbers_and_duration_do_not_lose_precision():
    assert json_value(18446744073709551615)=='18446744073709551615'
    assert json_value(dt.timedelta(hours=-25,minutes=-2,seconds=-3,microseconds=-4))=='-25:02:03.000004'
    assert json_value(dt.timedelta(hours=50,minutes=12))=='50:12:00'


def test_csv_roundtrip_null_empty_unicode_multiline_and_binary():
    rows=[{'text':'中文 😀\n"quoted",tab\t','empty':'','null':None,'marker':r'\N','slash':r'\\share','json':{'a':True},'binary':{'$binary':'00ff','size':2}}]
    encoded=csv_bytes(list(rows[0]),rows,delimiter=';')
    actual=parse_rows('sample.csv',encoded,delimiter=';')
    assert actual[0]|{'json':rows[0]['json'],'binary':rows[0]['binary']}==rows[0]
    assert actual[0]['json']=='{"a": true}'
    assert actual[0]['binary']=='0x00ff'


def test_csv_invalid_headers_shape_and_encoding_are_reported():
    for content in [b'a,a\n1,2',b'a,b\n1,2,3',b'a,b\n1']:
        with pytest.raises(DeeBeeError):parse_rows('a.csv',content)
    assert parse_rows('a.csv','name\n中文'.encode('gb18030'),encoding='gb18030')==[{'name':'中文'}]


def test_excel_preserves_precision_literal_formulas_empty_and_null():
    values={'big':'18446744073709551615','decimal':'1234567890.0123456789','formula':'=1+1','empty':'','null':None,'text':'中文\n😀','json':{'a':1},'binary':{'$binary':'00ff','size':2}}
    content=xlsx_bytes('invalid/name',list(values),[values])
    workbook=load_workbook(io.BytesIO(content))
    assert workbook.active['C2'].data_type=='s'
    assert workbook.active['A2'].value==values['big']
    assert workbook.active.freeze_panes=='A2'
    parsed=parse_rows('data.xlsx',content)[0]
    assert parsed['empty']=='' and parsed['null'] is None
    assert parsed['formula']=='=1+1'
    assert parsed['binary']=='0x00ff'
    assert json.loads(parsed['json'])=={'a':1}


def test_mapping_skips_columns_and_rejects_duplicates():
    target=[{'name':'name'},{'name':'n'},{'name':'computed','extra':'STORED GENERATED'}]
    rows=[{'source':'abc','skip':'unused'}]
    assert mapped_rows(rows,'{"source":"name","skip":""}',target)==[{'name':'abc'}]
    with pytest.raises(DeeBeeError):mapped_rows(rows,'{"source":"name","skip":"name"}',target)
    with pytest.raises(DeeBeeError):mapped_rows(rows,'{"source":"computed"}',target)


class DeadConnection:
    def __init__(self):self.closed=False;self.rollbacks=0
    def rollback(self):
        self.rollbacks+=1
        raise pymysql.OperationalError(2006,'MySQL server has gone away')
    def close(self):self.closed=True


def test_rename_keeps_live_sessions_and_changed_settings_discard_dead_sessions(tmp_path:Path):
    manager=DatabaseWorkbenches(store_path=tmp_path/'connections.json',defaults=[])
    profile=manager.create_connection({'driver':'mysql','name':'original','host':'test.invalid','port':3306,'user':'test','password':'test-only'})
    engine=manager._engine(profile['id']);dead=DeadConnection()
    engine.sessions['dead']=DbSession('dead',dead,profile['id'],'deebee_e2e',True,'test',threading.RLock())
    manager._session_engines['dead']=engine
    manager.update_connection(profile['id'],{'name':'renamed','password':None})
    assert 'dead' in engine.sessions and dead.rollbacks==0
    assert manager._store.list()[0]['name']=='renamed'
    manager.update_connection(profile['id'],{'host':'new-test.invalid','password':None})
    assert dead.closed and 'dead' not in engine.sessions and 'dead' not in manager._session_engines
    assert manager._store.list()[0]['password']=='test-only'


def test_batch_updates_use_one_transaction_and_rollback_the_entire_paste(tmp_path:Path,monkeypatch):
    manager=DatabaseWorkbenches(store_path=tmp_path/'connections.json',defaults=[])
    profile=manager.create_connection({'driver':'mysql','name':'test','host':'test.invalid','port':3306,'user':'test','password':'test-only'})
    engine=manager._engine(profile['id'])
    schema={'primary_key':['id'],'columns':[{'name':'id','data_type':'int','nullable':False},{'name':'n','data_type':'int','nullable':False}]}
    monkeypatch.setattr(engine,'table_schema',lambda *a:schema)
    state={'calls':0,'commits':0,'rollbacks':0}
    class Cursor:
        rowcount=1
        def __enter__(self):return self
        def __exit__(self,*a):return False
        def execute(self,*a):
            state['calls']+=1
            if state['calls']==2:raise pymysql.IntegrityError(1062,'duplicate test value')
    connection=SimpleNamespace(cursor=lambda:Cursor(),commit=lambda:state.__setitem__('commits',state['commits']+1),rollback=lambda:state.__setitem__('rollbacks',state['rollbacks']+1),close=lambda:None)
    monkeypatch.setattr(engine,'_connect',lambda *a,**k:connection)
    with pytest.raises(DeeBeeError):engine.update_rows(profile['id'],'deebee_e2e','test',[{'key':{'id':1},'changes':{'n':1}},{'key':{'id':2},'changes':{'n':1}}])
    assert state=={'calls':2,'commits':0,'rollbacks':1}


def test_json_validation_does_not_rewrite_numeric_lexemes():
    raw='{"i":18446744073709551615,"d":0.123456789012345678901}'
    assert mysql_value(raw,column('json'))==raw
    with pytest.raises(DeeBeeError):mysql_value('{"n":NaN}',column('json'))


def test_generated_detection_does_not_lock_default_timestamps():
    from deebee.data_values import is_generated_column
    assert not is_generated_column({'extra':'DEFAULT_GENERATED'})
    assert is_generated_column({'extra':'STORED GENERATED'})


def test_preview_mapping_export_and_batch_api_contracts(monkeypatch):
    from fastapi.testclient import TestClient
    from deebee.main import api_app, settings, workbench
    client=TestClient(api_app)
    login=client.post('/api/auth/login',json={'username':settings.admin_user,'password':settings.admin_password})
    headers={'Authorization':'Bearer '+login.json()['token']}
    columns=[{'name':'id','data_type':'bigint','nullable':False,'extra':''},{'name':'value','data_type':'varchar(120)','nullable':True,'extra':''},{'name':'created','data_type':'timestamp(6)','nullable':True,'extra':'DEFAULT_GENERATED'},{'name':'computed','data_type':'int','nullable':True,'extra':'STORED GENERATED'}]
    monkeypatch.setattr(workbench,'table_schema',lambda *a:{'columns':columns,'primary_key':['id']})
    monkeypatch.setattr(workbench,'profile_driver',lambda *a:'mysql')
    monkeypatch.setattr(workbench,'export_table',lambda *a:{'columns':columns,'rows':[{'id':'18446744073709551615','value':'=1+1','created':'2026-11-09 01:02:03.123456','computed':2}]})
    calls=[]
    monkeypatch.setattr(workbench,'bulk_insert',lambda *a,**k:calls.append(a) or {'affected_rows':1})
    monkeypatch.setattr(workbench,'update_rows',lambda *a:calls.append(a) or {'affected_rows':2})
    base={'profile_id':'mock','database':'deebee_e2e','table':'test'}
    preview=client.post('/api/data/import/preview',params={**base,'delimiter':';'},headers=headers,files={'file':('data.csv','source;ignored\n中文;skip'.encode(), 'text/csv')})
    assert preview.status_code==200,preview.text
    assert preview.json()['source_columns']==['source','ignored']
    assert [c['name'] for c in preview.json()['target_columns']]==['id','value','created']
    imported=client.post('/api/data/import',params={**base,'delimiter':';','mapping':'{"source":"value","ignored":""}'},headers=headers,files={'file':('data.csv','source;ignored\n中文;skip'.encode(),'text/csv')})
    assert imported.status_code==200,imported.text
    assert calls[-1][3]==[{'value':'中文'}]
    xlsx=client.get('/api/data/export',params={**base,'format':'xlsx'},headers=headers)
    assert xlsx.status_code==200,xlsx.text[:100] if xlsx.status_code!=200 else ''
    workbook=load_workbook(io.BytesIO(xlsx.content));sheet=workbook.active
    assert sheet['A2'].value=='18446744073709551615'
    assert sheet['B2'].data_type=='s' and sheet['B2'].value=='=1+1'
    sql=client.get('/api/data/export',params={**base,'format':'sql'},headers=headers)
    assert sql.status_code==200
    assert '`computed`' not in sql.text and '`created`' in sql.text
    csv=client.get('/api/data/export',params={**base,'format':'csv','columns':'["value","id"]','delimiter':';'},headers=headers)
    assert csv.status_code==200 and csv.content.startswith(b'\xef\xbb\xbfvalue;id')
    batch=client.patch('/api/data/rows/batch',headers=headers,json={**base,'schema':'tenant','edits':[{'key':{'id':1},'changes':{'value':'a'}},{'key':{'id':2},'changes':{'value':'b'}}]})
    assert batch.status_code==200,batch.text
    assert calls[-1][-1]=='tenant' and len(calls[-1][3])==2
    exported=client.post('/api/data/export-result',headers=headers,json={'profile_id':'mock','format':'csv','columns':[{'name':'value','type':'varchar'}],'rows':[{'value':'已选区域'}]})
    assert exported.status_code==200 and '已选区域' in exported.content.decode('utf-8-sig')


def test_excel_unsupported_content_and_corrupt_files_have_readable_errors():
    with pytest.raises(DeeBeeError,match='格式无效'):parse_rows('bad.xlsx',b'not an excel file')
    with pytest.raises(DeeBeeError,match='控制字符'):xlsx_bytes('data',['v'],[{'v':'before\x00after'}])
    with pytest.raises(DeeBeeError,match='字符限制'):xlsx_bytes('data',['v'],[{'v':'a'*32768}])


def test_sql_dump_excludes_computed_columns_and_formats_extended_time(tmp_path:Path,monkeypatch):
    manager=DatabaseWorkbenches(store_path=tmp_path/'connections.json',defaults=[])
    profile=manager.create_connection({'driver':'mysql','name':'test','host':'test.invalid','port':3306,'user':'test','password':'test-only'})
    engine=manager._engine(profile['id'])
    class Cursor:
        statement=''
        fetched=False
        def __enter__(self):return self
        def __exit__(self,*a):return False
        def execute(self,statement,*args):self.statement=statement
        def fetchone(self):return {'Create Table':'CREATE TABLE `test` (id INT, clock TIME, computed INT GENERATED ALWAYS AS (id*2))'}
        def fetchall(self):return [{'name':'id','extra':''},{'name':'clock','extra':''},{'name':'computed','extra':'VIRTUAL GENERATED'}]
        def fetchmany(self,_size):
            if self.fetched:return []
            self.fetched=True
            assert self.statement=='SELECT `id`, `clock` FROM `deebee_e2e`.`test`'
            return [{'id':1,'clock':dt.timedelta(hours=-25,minutes=-2)}]
    monkeypatch.setattr(engine,'_connect',lambda *a,**k:SimpleNamespace(cursor=lambda:Cursor(),close=lambda:None))
    dumped=engine.dump_sql(profile['id'],'deebee_e2e','test')
    insert=next(line for line in dumped.splitlines() if line.startswith('INSERT'))
    assert '`computed`' not in insert and "'-25:02:00'" in insert
