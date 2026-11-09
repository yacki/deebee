"""Server-owned, bounded protocol recordings. No desktop capture or transcoding."""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
import threading
import time
import uuid
import zlib
from pathlib import Path
from typing import Any, Iterator

from .mysql import DeeBeeError


class RecordingStore:
    def __init__(self, directory: Path, *, quota: int = 5 * 1024**3,
                 session_limit: int = 128 * 1024**2, retention_days: int = 30,
                 min_free: int = 256 * 1024**2):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
        self.quota, self.session_limit = quota, session_limit
        self.retention_days, self.min_free = retention_days, min_free
        self.lock = threading.RLock()
        self.db = sqlite3.connect(directory / 'index.sqlite3', check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('''CREATE TABLE IF NOT EXISTS recordings (
            id TEXT PRIMARY KEY, protocol TEXT NOT NULL, actor TEXT NOT NULL,
            profile_id TEXT NOT NULL, target TEXT NOT NULL, host TEXT NOT NULL,
            username TEXT NOT NULL, started_at REAL NOT NULL, ended_at REAL,
            duration REAL NOT NULL DEFAULT 0, status TEXT NOT NULL,
            raw_bytes INTEGER NOT NULL DEFAULT 0, stored_bytes INTEGER NOT NULL DEFAULT 0,
            error TEXT NOT NULL DEFAULT '', sha256 TEXT NOT NULL DEFAULT '')''')
        self.db.execute('CREATE INDEX IF NOT EXISTS recording_time ON recordings(started_at DESC)')
        self.db.commit()
        (directory / 'index.sqlite3').chmod(0o600)

    def path(self, recording_id: str) -> Path:
        if not re.fullmatch(r'[a-f0-9]{32}', recording_id):
            raise DeeBeeError('录像不存在')
        return self.directory / (recording_id + '.gz')

    def recover(self) -> None:
        """Keep complete gzip members after an unclean shutdown; never replay a partial frame."""
        with self.lock:
            for row in self.db.execute("SELECT * FROM recordings WHERE status='recording'").fetchall():
                path = self.path(row['id'])
                complete = complete_raw = member_raw = 0
                if path.exists():
                    decoder = zlib.decompressobj(31)
                    consumed = 0
                    with path.open('rb') as source:
                        while chunk := source.read(65536):
                            while chunk:
                                try:
                                    raw = decoder.decompress(chunk)
                                except zlib.error:
                                    chunk = b''
                                    break
                                consumed += len(chunk) - len(decoder.unused_data)
                                member_raw += len(raw)
                                chunk = decoder.unused_data
                                if decoder.eof:
                                    complete = consumed
                                    complete_raw += member_raw
                                    member_raw = 0
                                    decoder = zlib.decompressobj(31)
                                else:
                                    break
                    with path.open('r+b') as target:
                        target.truncate(complete)
                self.db.execute("UPDATE recordings SET status='interrupted', ended_at=?, stored_bytes=?, raw_bytes=?, error=? WHERE id=?",
                                (time.time(), complete, complete_raw, '服务重启，保留已写入的录像', row['id']))
            self.db.commit()
            self.cleanup()

    def cleanup(self) -> None:
        with self.lock:
            if self.retention_days <= 0:
                return
            cutoff = time.time() - self.retention_days * 86400
            expired = self.db.execute("SELECT id FROM recordings WHERE ended_at < ? AND status != 'recording'", (cutoff,)).fetchall()
            for row in expired:
                self.path(row['id']).unlink(missing_ok=True)
                self.db.execute('DELETE FROM recordings WHERE id=?', (row['id'],))
            self.db.commit()

    def used(self) -> int:
        return int(self.db.execute('SELECT COALESCE(SUM(stored_bytes),0) FROM recordings').fetchone()[0])

    def create(self, protocol: str, actor: str, profile: Any) -> str:
        with self.lock:
            self.cleanup()
            if self.used() >= self.quota or shutil.disk_usage(self.directory).free < self.min_free:
                raise DeeBeeError('录像存储空间不足，请联系管理员；未建立远程会话')
            recording_id = uuid.uuid4().hex
            descriptor = os.open(self.path(recording_id), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
            self.db.execute('''INSERT INTO recordings
                (id,protocol,actor,profile_id,target,host,username,started_at,status)
                VALUES(?,?,?,?,?,?,?,?,?)''',
                (recording_id, protocol, actor, profile.id, profile.name, profile.host,
                 profile.user, time.time(), 'recording'))
            self.db.commit()
            return recording_id

    def write(self, recording_id: str, raw: bytes, duration: float) -> None:
        compressed = gzip.compress(raw, compresslevel=1, mtime=0)
        with self.lock:
            row = self.db.execute('SELECT raw_bytes FROM recordings WHERE id=?', (recording_id,)).fetchone()
            if row['raw_bytes'] + len(raw) > self.session_limit:
                raise DeeBeeError('单次录像已达到容量上限，会话已停止；请新建会话')
            if self.used() + len(compressed) > self.quota or shutil.disk_usage(self.directory).free < self.min_free:
                raise DeeBeeError('录像存储空间不足，会话已停止，请联系管理员')
            with self.path(recording_id).open('ab') as target:
                target.write(compressed)
            self.db.execute('UPDATE recordings SET raw_bytes=raw_bytes+?,stored_bytes=stored_bytes+?,duration=? WHERE id=?',
                            (len(raw), len(compressed), duration, recording_id))
            self.db.commit()

    def finish(self, recording_id: str, duration: float, error: str = '') -> None:
        with self.lock:
            path = self.path(recording_id)
            committed_size = self.db.execute('SELECT stored_bytes FROM recordings WHERE id=?', (recording_id,)).fetchone()[0]
            # A failed disk write must not leave a partial gzip member in a downloadable file.
            if path.stat().st_size != committed_size:
                with path.open('r+b') as target:
                    target.truncate(committed_size)
            digest = hashlib.sha256()
            with path.open('rb') as source:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
                os.fsync(source.fileno())
            self.db.execute('UPDATE recordings SET ended_at=?,duration=?,status=?,error=?,sha256=? WHERE id=?',
                            (time.time(), duration, 'failed' if error else 'completed', error[:500], digest.hexdigest(), recording_id))
            self.db.commit()

    def get(self, recording_id: str) -> dict:
        self.path(recording_id)
        with self.lock:
            row = self.db.execute('SELECT * FROM recordings WHERE id=?', (recording_id,)).fetchone()
            if not row:
                raise DeeBeeError('录像不存在或已过期')
            return dict(row)

    def list(self, *, protocol: str = '', q: str = '', status: str = '',
             after: float = 0, before: float = 0, offset: int = 0, limit: int = 50) -> dict:
        clauses, args = ['1=1'], []
        for column, value in [('protocol', protocol), ('status', status)]:
            if value:
                clauses.append(column + '=?'); args.append(value)
        if q:
            clauses.append("(instr(lower(actor || ' ' || target || ' ' || host || ' ' || username),lower(?))>0)"); args.append(q)
        if after:
            clauses.append('started_at>=?'); args.append(after)
        if before:
            clauses.append('started_at<=?'); args.append(before)
        where = ' AND '.join(clauses)
        with self.lock:
            self.cleanup()
            total = self.db.execute('SELECT count(*) FROM recordings WHERE ' + where, args).fetchone()[0]
            items = [dict(row) for row in self.db.execute('SELECT * FROM recordings WHERE ' + where + ' ORDER BY started_at DESC LIMIT ? OFFSET ?', [*args, limit, offset])]
            for item in items:
                if item['status'] == 'recording':
                    item['duration'] = round(time.time() - item['started_at'], 3)
            return {'items': items, 'total': total, 'storage': {'used_bytes': self.used(), 'quota_bytes': self.quota,
                    'session_limit_bytes': self.session_limit, 'retention_days': self.retention_days}}

    def content(self, recording_id: str) -> Iterator[bytes]:
        with gzip.open(self.path(recording_id), 'rb') as source:
            while chunk := source.read(65536):
                yield chunk


class SessionRecorder:
    def __init__(self, store: RecordingStore, recording_id: str):
        self.store, self.id = store, recording_id
        self.started = time.monotonic()
        self.queued = 0
        self.image_streams: set[str] = set()
        self.queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.task = asyncio.create_task(self._pump())

    @classmethod
    async def start(cls, store: RecordingStore, protocol: str, actor: str, profile: Any) -> 'SessionRecorder':
        recording_id = await asyncio.to_thread(store.create, protocol, actor, profile)
        return cls(store, recording_id)

    def append(self, data: bytes) -> None:
        if self.task.done():
            self.task.result()
            raise DeeBeeError('录像已停止')
        if self.queued + len(data) > 2 * 1024**2:
            raise DeeBeeError('录像写入速度不足，会话已停止，请联系管理员')
        self.queued += len(data)
        self.queue.put_nowait(data)

    def event(self, kind: str, data: str) -> None:
        self.append((json.dumps([round(time.monotonic() - self.started, 3), kind, data], ensure_ascii=False) + '\n').encode())

    def screen(self, opcode: str, arguments: list[str], raw: bytes) -> None:
        # Only display operations. Clipboard, files, audio and keystrokes are not part of the picture.
        visual = {"arc", "cfill", "clip", "close", "copy", "cstroke", "cursor", "curve",
                  "dispose", "distort", "identity", "img", "jpeg", "lfill", "line", "lstroke",
                  "move", "png", "pop", "push", "rect", "reset", "set", "shade", "size",
                  "start", "sync", "transfer", "transform"}
        if opcode == "img" and arguments:
            self.image_streams.add(arguments[0])
        if opcode in {"blob", "end"} and arguments and arguments[0] in self.image_streams:
            if opcode == "end":
                self.image_streams.discard(arguments[0])
            self.append(raw)
        elif opcode in visual:
            self.append(raw)

    async def _pump(self) -> None:
        pending: list[bytes] = []
        size = 0
        last = time.monotonic()
        while True:
            try:
                value = await asyncio.wait_for(self.queue.get(), timeout=max(.01, 1 - (time.monotonic() - last)))
            except TimeoutError:
                value = b''
            if value:
                pending.append(value); size += len(value)
            if size and (value is None or size >= 131072 or time.monotonic() - last >= 1):
                await asyncio.to_thread(self.store.write, self.id, b''.join(pending), time.monotonic() - self.started)
                self.queued -= size
                pending = []; size = 0
            if value is None:
                break
            if time.monotonic() - last >= 1:
                last = time.monotonic()

    async def close(self, error: str = '') -> None:
        self.queue.put_nowait(None)
        try:
            await self.task
        except Exception as exc:
            error = str(exc) if isinstance(exc, DeeBeeError) else '录像文件写入失败'
        await asyncio.to_thread(self.store.finish, self.id, time.monotonic() - self.started, error)
