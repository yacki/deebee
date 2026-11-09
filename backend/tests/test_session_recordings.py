from __future__ import annotations

import asyncio
import gzip
import json
import os
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from deebee.access.api import install_access
from deebee.config import settings
from deebee.mysql import DeeBeeError
from deebee.recordings import RecordingStore, SessionRecorder
from deebee.remote_connections import encode_instruction, read_instruction
from deebee.security import issue_token


def profile():
    return SimpleNamespace(id='linux-test', name='Linux 测试', host='100.80.0.19', user='operator', password='never-record-this')


def store(tmp_path, **kwargs):
    return RecordingStore(tmp_path / 'recordings', min_free=0, **kwargs)


def test_terminal_roundtrip_resize_and_private_files(tmp_path):
    storage = store(tmp_path)
    async def session():
        recorder = await SessionRecorder.start(storage, 'ssh', 'alice', profile())
        recorder.append(b'{"version":2,"width":80,"height":24}\n')
        recorder.event('o', '\x1b[32mLinux connected 中文\r\n')
        recorder.event('r', '100x30')
        await recorder.close()
        return recorder.id
    recording_id = asyncio.run(session())
    row = storage.get(recording_id)
    assert row['status'] == 'completed' and row['sha256']
    raw = b''.join(storage.content(recording_id))
    assert 'Linux connected 中文' in raw.decode() and b'never-record-this' not in raw
    assert json.loads(raw.splitlines()[-1])[1:] == ['r', '100x30']
    assert os.stat(storage.path(recording_id)).st_mode & 0o777 == 0o600
    assert row['stored_bytes'] == storage.path(recording_id).stat().st_size
    with pytest.raises(DeeBeeError): storage.path('../../secret')


def test_rdp_replay_preserves_graphics_but_excludes_clipboard_audio_and_keys(tmp_path):
    storage = store(tmp_path)
    instructions = [('size', ['0','800','600']), ('clipboard', ['7','text/plain']),
                    ('blob', ['7','secret-clipboard']), ('end',['7']),
                    ('img', ['8','14','0','image/png','0','0']), ('blob',['8','aW1hZ2U=']), ('end',['8']),
                    ('audio',['9','audio/ogg']),('blob',['9','secret-audio']),('end',['9']),
                    ('key',['100','1']), ('sync',['123']), ('sync',['223'])]
    async def session():
        recorder = await SessionRecorder.start(storage,'rdp','admin',profile())
        for opcode,args in instructions:recorder.screen(opcode,args,encode_instruction(opcode,*args))
        await recorder.close()
        reader=asyncio.StreamReader();reader.feed_data(b''.join(storage.content(recorder.id)));reader.feed_eof()
        result=[]
        while not reader.at_eof():
            opcode,args,_=await read_instruction(reader);result.append((opcode,args))
        return result
    result=asyncio.run(session())
    assert result == [item for item in instructions if item[0] in {'size','img','sync'} or item[1][0]=='8']


def test_quota_and_writer_failure_remain_visible_and_stop_capture(tmp_path):
    storage=store(tmp_path,session_limit=100)
    async def session():
        recorder=await SessionRecorder.start(storage,'ssh','admin',profile())
        recorder.append(b'x'*200)
        await recorder.close()
        assert storage.get(recorder.id)['status']=='failed'
        assert '容量上限' in storage.get(recorder.id)['error']
    asyncio.run(session())
    full=RecordingStore(tmp_path/'full',quota=0,min_free=0)
    with pytest.raises(DeeBeeError,match='空间不足'):full.create('rdp','admin',profile())


def test_write_backpressure_is_bounded(tmp_path):
    async def session():
        recorder=await SessionRecorder.start(store(tmp_path),'ssh','admin',profile())
        with pytest.raises(DeeBeeError,match='写入速度不足'):recorder.append(b'x'*(2*1024**2+1))
        await recorder.close('overflow')
    asyncio.run(session())


def test_restart_recovers_only_complete_compressed_batches(tmp_path):
    storage=store(tmp_path)
    rid=storage.create('ssh','admin',profile())
    storage.write(rid,b'first complete batch\n',1)
    with storage.path(rid).open('ab') as target:target.write(gzip.compress(b'incomplete last batch')[:-7])
    storage.recover()
    assert storage.get(rid)['status']=='interrupted'
    assert b''.join(storage.content(rid))==b'first complete batch\n'
    assert storage.get(rid)['stored_bytes']==storage.path(rid).stat().st_size


def test_retention_filters_and_active_session_protection(tmp_path):
    storage=store(tmp_path,retention_days=1)
    old=storage.create('ssh','alice',profile());storage.finish(old,1)
    active=storage.create('rdp','bob',profile())
    with storage.lock:
        storage.db.execute('UPDATE recordings SET ended_at=? WHERE id=?',(time.time()-2*86400,old));storage.db.commit()
    assert storage.list(q='BOB',protocol='rdp')['total']==1
    assert storage.list(q='nonexistent')['total']==0
    assert storage.list()['total']==1
    assert not storage.path(old).exists() and storage.path(active).exists()


def test_recording_management_requires_local_admin_for_list_playback_and_download(tmp_path,monkeypatch):
    monkeypatch.setenv('DEEBEE_RECORDINGS_DIR',str(tmp_path/'files'))
    app=FastAPI();install_access(app,tmp_path/'access')
    storage=app.state.recordings;rid=storage.create('ssh','operator',profile())
    storage.write(rid,b'{"version":2,"width":80,"height":24}\n[0,"o","hello"]\n',1)
    admin={'Authorization':'Bearer '+issue_token(settings.admin_user)}
    other={'Authorization':'Bearer '+issue_token('other-user')}
    with TestClient(app) as client:
        for suffix in ['',f'/{rid}/content',f'/{rid}/download']:
            path='/api/admin/v1/session-recordings'+suffix
            assert client.get(path).status_code==401
            assert client.get(path,headers=other).status_code==401
            assert client.get(path,headers={'X-Deebee-Api-Key':'fake-key'}).status_code==403
        # Lifespan marks the pre-start fixture interrupted, preserving its content.
        assert client.get('/api/admin/v1/session-recordings',headers=admin).json()['items'][0]['id']==rid
        response=client.get(f'/api/admin/v1/session-recordings/{rid}/content',headers=admin)
        assert response.status_code==200 and response.headers['cache-control']=='no-store'
        assert b'hello' in response.content
        response=client.get(f'/api/admin/v1/session-recordings/{rid}/download',headers=admin)
        assert gzip.decompress(response.content).endswith(b'[0,"o","hello"]\n')
        active=storage.create('rdp','admin',profile())
        assert client.get(f'/api/admin/v1/session-recordings/{active}/content',headers=admin).status_code==409
        assert client.get('/api/admin/v1/session-recordings/not-an-id/content',headers=admin).status_code==404


def test_partial_disk_write_preserves_the_last_playable_batch(tmp_path,monkeypatch):
    storage=store(tmp_path)
    async def session():
        recorder=await SessionRecorder.start(storage,'ssh','admin',profile())
        storage.write(recorder.id,b'complete frame\n',1)
        def broken_write(recording_id,raw,duration):
            with storage.path(recording_id).open('ab') as target:target.write(b'partial-gzip')
            raise OSError('simulated full disk')
        monkeypatch.setattr(storage,'write',broken_write)
        recorder.append(b'new frame')
        await recorder.close()
        assert storage.get(recorder.id)['status']=='failed'
        assert b''.join(storage.content(recorder.id))==b'complete frame\n'
    asyncio.run(session())


def test_prefixed_ssh_websocket_records_on_the_inner_application(tmp_path,monkeypatch):
    from deebee import main
    from deebee.remote_connections import RemoteProfile
    storage=store(tmp_path)
    monkeypatch.setattr(main.api_app.state,'recordings',storage)
    target=RemoteProfile('ssh-real','ssh','Linux','localhost',22,'operator','secret')
    monkeypatch.setattr(main.workbench,'remote_profile',lambda *args:target)
    class Output:
        sent=False
        async def read(self,size):
            if not self.sent:self.sent=True;return 'Recorded actual SSH stream\r\n'
            await asyncio.Event().wait()
    class Process:
        stdout=Output()
        stdin=SimpleNamespace(write=lambda value:None)
        def change_terminal_size(self,*args):pass
        def close(self):pass
    class Connection:
        async def create_process(self,**kwargs):return Process()
        def get_extra_info(self,*args):return 'SSH-Test'
        def close(self):pass
        async def wait_closed(self):pass
    async def connect(*args):return Connection()
    monkeypatch.setattr(main,'open_ssh',connect)
    client=TestClient(main.app)
    path=settings.base_path+'/api/connections/ssh-real/ssh?token='+issue_token(settings.admin_user)
    with client.websocket_connect(path) as ws:
        assert ws.receive_json()['state']=='connecting'
        assert ws.receive_json()['state']=='connected'
        assert 'Recorded actual SSH stream' in ws.receive_json()['data']
        ws.send_json({'type':'resize','cols':100,'rows':30})
        ws.send_json({'type':'close'})
        assert ws.receive()['type']=='websocket.close'
        # The recorder flushes after the transport is closed.
        for _ in range(100):
            if storage.list()['items'][0]['status']!='recording':break
            time.sleep(.01)
    item=storage.list()['items'][0]
    assert item['status']=='completed'
    assert b'Recorded actual SSH stream' in b''.join(storage.content(item['id']))


def test_prefixed_rdp_websocket_records_gateway_graphics(tmp_path,monkeypatch):
    from deebee import main
    from deebee.remote_connections import RemoteProfile
    storage=store(tmp_path);monkeypatch.setattr(main.api_app.state,'recordings',storage)
    monkeypatch.setattr(main.workbench,'remote_profile',lambda *args:RemoteProfile('rdp-real','rdp','Windows','localhost',3389,'operator','secret'))
    payload=[('size',['0','320','200']),('sync',['100']),('rect',['0','0','0','320','200']),('cfill',['14','0','30','110','210','255']),('sync',['200'])]
    class Writer:
        def write(self,data):pass
        async def drain(self):pass
    class Tunnel:
        connection_id='test-recording'
        writer=Writer()
        async def close(self):pass
    async def connect(*args,**kwargs):
        tunnel=Tunnel();tunnel.reader=asyncio.StreamReader()
        tunnel.reader.feed_data(b''.join(encode_instruction(op,*args) for op,args in payload))
        return tunnel
    monkeypatch.setattr(main.GuacdTunnel,'connect',connect)
    client=TestClient(main.app)
    path=settings.base_path+'/api/connections/rdp-real/rdp?token='+issue_token(settings.admin_user)
    with client.websocket_connect(path) as ws:
        assert 'test-recording' in ws.receive_text()
        for _ in payload:ws.receive_text()
        ws.close()
        for _ in range(100):
            if storage.list()['items'][0]['status']!='recording':break
            time.sleep(.01)
    row=storage.list()['items'][0]
    assert row['status']=='completed'
    assert b''.join(storage.content(row['id']))==b''.join(encode_instruction(op,*args) for op,args in payload)
