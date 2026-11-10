"""Durable, identity-bound background jobs shared by HTTP and future adapters.

Business results stay in the tenant database. The control queue stores only an
allowlisted operation and immutable identity envelope, never a user-supplied
connection or executable callable. Leases permit recovery after process death.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
import uuid
from contextlib import closing
from contextvars import ContextVar
from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from .security.context import Principal, require_context, use_context
from .security.store import control_connection, assert_principal_current, assert_job_principal_current

router = APIRouter()
ALLOWED = {'material_extract', 'experience_analysis', 'special_refinement'}
ACTIVE = ('queued', 'running', 'retry')
_job_lease = ContextVar('ontology_job_lease', default=None)
_schema_lock = threading.Lock()
_schema_ready = set()


def ensure_queue():
    from .db import mysql_config
    config=mysql_config()
    signature=(config['host'],config['port'],config['database'])
    if signature in _schema_ready:
        return
    with _schema_lock:
        if signature in _schema_ready:
            return
        with control_connection() as c, c.cursor() as cur:
            cur.execute('''CREATE TABLE IF NOT EXISTS ontology_task_queue (
                job_id VARCHAR(191) PRIMARY KEY,tenant_id VARCHAR(100) NOT NULL,
                kind VARCHAR(64) NOT NULL,entity_id VARCHAR(191) NOT NULL,
                principal_json LONGTEXT NOT NULL,args_json LONGTEXT NOT NULL,
                status VARCHAR(24) NOT NULL,attempts INT NOT NULL DEFAULT 0,
                max_attempts INT NOT NULL DEFAULT 3,available_at DATETIME(6) NOT NULL,
                lease_token VARCHAR(64),lease_until DATETIME(6),active_key CHAR(64) UNIQUE,
                last_error VARCHAR(500) NOT NULL DEFAULT '',created_at DATETIME(6) NOT NULL,
                updated_at DATETIME(6) NOT NULL,
                INDEX idx_queue_ready(status,available_at,lease_until),
                INDEX idx_queue_tenant(tenant_id,entity_id,status)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci''')
        _schema_ready.add(signature)


def _slot(tenant, kind, entity):
    return hashlib.sha256(f'{tenant}\0{kind}\0{entity}'.encode()).hexdigest()


def enqueue(job_id, kind, entity_id, args, *, retry=False):
    """Call inside the tenant transaction, before committing its job record."""
    if require_context().identity_kind != 'session':
        raise HTTPException(403, '应用身份不能提交后台任务。')
    if kind not in ALLOWED or not isinstance(args, list):
        raise ValueError('Unsupported background operation')
    p = assert_principal_current(require_context(), require_write=True)
    ensure_queue()
    encoded = json.dumps(asdict(p), ensure_ascii=False)
    payload = json.dumps(args, ensure_ascii=False)
    if len(payload) > 32000:
        raise HTTPException(422, '后台任务参数过大。')
    with control_connection() as c, c.cursor() as cur:
        cur.execute('SELECT * FROM ontology_task_queue WHERE job_id=%s FOR UPDATE', (job_id,))
        old = cur.fetchone()
        if old:
            if old['tenant_id'] != p.tenant_id or old['kind'] != kind or old['entity_id'] != entity_id:
                raise HTTPException(404, '未找到该机构任务。')
            if old['status'] in ACTIVE:
                return {'id': job_id, 'status': old['status'], 'already_queued': True}
            if not retry:
                return {'id': job_id, 'status': old['status'], 'already_queued': True}
        slot = _slot(p.tenant_id, kind, entity_id)
        cur.execute('SELECT job_id FROM ontology_task_queue WHERE active_key=%s FOR UPDATE', (slot,))
        conflict = cur.fetchone()
        if conflict and conflict['job_id'] != job_id:
            raise HTTPException(409, '该对象已有后台任务，请等待完成后重试。')
        try:
            if old:
                cur.execute("UPDATE ontology_task_queue SET principal_json=%s,args_json=%s,status='queued',attempts=0,available_at=DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 2 SECOND),lease_token=NULL,lease_until=NULL,active_key=%s,last_error='',updated_at=UTC_TIMESTAMP(6) WHERE job_id=%s", (encoded,payload,slot,job_id))
            else:
                cur.execute("INSERT INTO ontology_task_queue(job_id,tenant_id,kind,entity_id,principal_json,args_json,status,available_at,active_key,created_at,updated_at) VALUES(%s,%s,%s,%s,%s,%s,'queued',DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 2 SECOND),%s,UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))", (job_id,p.tenant_id,kind,entity_id,encoded,payload,slot))
        except Exception as exc:
            if getattr(exc, 'args', [None])[0] == 1062:
                raise HTTPException(409, '同一对象已有待处理任务。') from exc
            raise
    return {'id': job_id, 'status': 'queued'}


def active_for(kind, entity_id):
    p = require_context()
    ensure_queue()
    with control_connection() as c, c.cursor() as cur:
        cur.execute("SELECT 1 FROM ontology_task_queue WHERE tenant_id=%s AND kind=%s AND entity_id=%s AND status IN ('queued','running','retry') LIMIT 1", (p.tenant_id,kind,entity_id))
        return bool(cur.fetchone())


def queue_status(job_id):
    p = require_context()
    ensure_queue()
    with control_connection() as c, c.cursor() as cur:
        cur.execute('SELECT job_id,status,attempts,max_attempts,last_error,created_at,updated_at FROM ontology_task_queue WHERE job_id=%s AND tenant_id=%s', (job_id,p.tenant_id))
        row = cur.fetchone()
    return row



def display_job(job):
    """Read-only overlay: revoked tasks cannot write their former tenant records."""
    queued=queue_status(job['id'])
    result=dict(job)
    if queued:
        result['queue']=queued
        if queued['status'] in ACTIVE:
            result['status']='queued' if queued['status'] in ('queued','retry') else 'running'
        elif queued['status'] in ('failed','cancelled'):
            result.update(status='failed',message=queued['last_error'])
    return result


def display_analysis(analysis):
    result=dict(analysis)
    queued=queue_status(result.get('run_id','')) if result.get('run_id') else None
    if queued:
        result['queue']=queued
        if queued['status'] in ACTIVE:
            result.update(status='running',message='任务已持久保存，正在处理或等待重试。')
        elif queued['status'] in ('failed','cancelled'):
            result.update(status='interrupted',message=queued['last_error'])
    return result

def assert_job_lease_current():
    """db.commit calls this before committing any worker business transaction."""
    lease = _job_lease.get()
    if lease is None:
        return
    job_id, token = lease
    with control_connection() as c, c.cursor() as cur:
        cur.execute("SELECT 1 FROM ontology_task_queue WHERE job_id=%s AND tenant_id=%s AND lease_token=%s AND status='running' AND lease_until>UTC_TIMESTAMP(6)", (job_id,require_context().tenant_id,token))
        if not cur.fetchone():
            raise HTTPException(409, '后台任务租约已经失效，本轮写入已拒绝。')


def assert_execution_principal_current(principal, require_write=False):
    """Only a valid server-owned worker lease may retain its captured workspace."""
    if _job_lease.get() is None:
        return assert_principal_current(principal, require_write=require_write)
    assert_job_lease_current()
    return assert_job_principal_current(principal, require_write=require_write)


def _claim():
    ensure_queue()
    with control_connection() as c, c.cursor() as cur:
        cur.execute("SELECT * FROM ontology_task_queue WHERE ((status IN ('queued','retry') AND available_at<=UTC_TIMESTAMP(6)) OR (status='running' AND lease_until<UTC_TIMESTAMP(6))) AND attempts<max_attempts ORDER BY available_at,created_at LIMIT 1 FOR UPDATE SKIP LOCKED")
        row = cur.fetchone()
        if not row:
            # A worker that died during its final allowed attempt cannot hold an object forever.
            cur.execute("UPDATE ontology_task_queue SET status='failed',active_key=NULL,last_error='任务执行已中断，请重新提交重试。',updated_at=UTC_TIMESTAMP(6) WHERE status='running' AND lease_until<UTC_TIMESTAMP(6) AND attempts>=max_attempts")
            return None
        token = uuid.uuid4().hex
        cur.execute("UPDATE ontology_task_queue SET status='running',attempts=attempts+1,lease_token=%s,lease_until=DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 60 SECOND),updated_at=UTC_TIMESTAMP(6) WHERE job_id=%s", (token,row['job_id']))
        row.update(lease_token=token,attempts=row['attempts']+1)
        return row


def _touch(row):
    with control_connection() as c, c.cursor() as cur:
        cur.execute("UPDATE ontology_task_queue SET lease_until=DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 60 SECOND),updated_at=UTC_TIMESTAMP(6) WHERE job_id=%s AND lease_token=%s AND status='running'", (row['job_id'],row['lease_token']))
        return cur.rowcount == 1


def _finish(row, status, message=''):
    retrying = status == 'retry'
    with control_connection() as c, c.cursor() as cur:
        cur.execute("UPDATE ontology_task_queue SET status=%s,last_error=%s,lease_until=NULL,lease_token=NULL,active_key=IF(%s,active_key,NULL),available_at=DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 10 SECOND),updated_at=UTC_TIMESTAMP(6) WHERE job_id=%s AND lease_token=%s", (status,message[:500],retrying,row['job_id'],row['lease_token']))


async def _heartbeat(row, principal):
    while True:
        await asyncio.sleep(15)
        await asyncio.to_thread(assert_execution_principal_current, principal, require_write=True)
        if not await asyncio.to_thread(_touch, row):
            raise HTTPException(409, '任务租约失效。')


async def _invoke(row):
    from .db import connect
    p = Principal(**json.loads(row['principal_json']))
    if p.tenant_id != row['tenant_id']:
        raise HTTPException(403, '任务机构上下文不匹配。')
    with use_context(p):
        token = _job_lease.set((row['job_id'],row['lease_token']))
        try:
            p = assert_execution_principal_current(p, require_write=True)
            with closing(connect()) as c:
                job = c.execute('SELECT status FROM jobs WHERE id=? AND kind=? AND entity_id=?', (row['job_id'],row['kind'],row['entity_id'])).fetchone()
                if not job:
                    raise HTTPException(409, '任务业务事务未完成。')
                if job['status'] == 'completed':
                    return 'completed'
            if row['kind'] == 'material_extract':
                from .materials import run_extraction
                args=json.loads(row['args_json'])
                if row['attempts'] > 1: args[3]=True
                operation=run_extraction(*args)
            elif row['kind'] == 'experience_analysis':
                from .experience import run_record_analysis
                operation=run_record_analysis(*json.loads(row['args_json']))
            elif row['kind'] == 'special_refinement':
                from .special_projects import run_refinement
                operation=run_refinement(*json.loads(row['args_json']))
            else:
                raise ValueError('Unsupported operation')
            task=asyncio.create_task(operation)
            heartbeat=asyncio.create_task(_heartbeat(row,p))
            try:
                done,_=await asyncio.wait({task,heartbeat},return_when=asyncio.FIRST_COMPLETED)
                if heartbeat in done:
                    await heartbeat
                await task
                assert_execution_principal_current(p,require_write=True)
                assert_job_lease_current()
            finally:
                for running in (task,heartbeat):
                    if not running.done():running.cancel()
                await asyncio.gather(task,heartbeat,return_exceptions=True)
            with closing(connect()) as c:
                end=c.execute('SELECT status FROM jobs WHERE id=?',(row['job_id'],)).fetchone()
                return end['status'] if end else 'failed'
        finally:
            _job_lease.reset(token)


async def run_one():
    row=await asyncio.to_thread(_claim)
    if row is None:
        return False
    try:
        status=await _invoke(row)
        if status in ('failed','partial','interrupted'):
            _finish(row,'retry' if row['attempts']<row['max_attempts'] else 'failed','部分处理未完成，可重试；已完成内容保留。')
        else:
            _finish(row,'completed' if status=='completed' else 'failed')
    except HTTPException as exc:
        _finish(row,'cancelled' if exc.status_code in (401,403) else 'retry' if row['attempts']<row['max_attempts'] else 'failed',
                '原提交者会话或授权已失效，任务停止。' if exc.status_code in (401,403) else '任务未完成，可重试。')
    except asyncio.CancelledError:
        # Leave the lease for safe recovery; another worker cannot write until expiry.
        raise
    except Exception:
        _finish(row,'retry' if row['attempts']<row['max_attempts'] else 'failed','任务执行中断，已保存结果保留。')
    return True


async def worker_loop(stop=None, concurrency=None):
    """Use once per service process, or run scripts/run_worker.py separately."""
    limit=max(1,min(int(concurrency or os.getenv('ONTOLOGY_WORKER_CONCURRENCY','4')),16))
    async def worker():
        while stop is None or not stop.is_set():
            try:
                worked=await run_one()
            except asyncio.CancelledError:
                raise
            except Exception:
                worked=False
            if not worked:
                await asyncio.sleep(1)
    tasks=[asyncio.create_task(worker()) for _ in range(limit)]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)


@router.post('/jobs/{job_id}/retry')
def retry_job(job_id:str):
    p=assert_principal_current(require_context(),require_write=True)
    if p.role not in ('admin','modeler'):
        raise HTTPException(403,'仅建模人员可重试任务。')
    ensure_queue()
    with control_connection() as c, c.cursor() as cur:
        cur.execute('SELECT * FROM ontology_task_queue WHERE job_id=%s AND tenant_id=%s',(job_id,p.tenant_id))
        row=cur.fetchone()
    if not row:
        raise HTTPException(404,'未找到该机构任务。')
    if row['status'] in ACTIVE:
        return {'id':job_id,'status':row['status'],'already_queued':True}
    if row['status']=='completed':
        raise HTTPException(409,'任务已完成，无需重试。')
    from .db import connect,now
    with closing(connect()) as c,c:
        existing=c.execute('SELECT id FROM jobs WHERE id=?',(job_id,)).fetchone()
        if not existing:raise HTTPException(404,'原任务已不存在。')
        c.execute("UPDATE jobs SET status='queued',message='等待重新处理',updated_at=? WHERE id=?",(now(),job_id))
        return enqueue(job_id,row['kind'],row['entity_id'],json.loads(row['args_json']),retry=True)
