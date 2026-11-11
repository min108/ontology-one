"""Opt-in durable queue, tenant storage/cache and revocation regression.

RUN_JOBS_MYSQL_TESTS=1 MYSQL_HOST=... MYSQL_PORT=... MYSQL_USER=...
MYSQL_PASSWORD=... python -m unittest discover -s tests -p test_jobs_mysql.py

Use an isolated test MySQL server with schema/trigger deployment privileges.
Creates a random database namespace and removes only that namespace. No test
credentials or tenant workspaces are persisted in the source tree.
"""
import asyncio
import hashlib
import io
import json
import os
import secrets
import shutil
import tempfile
import threading
import unittest
import zipfile
from contextlib import closing
from unittest.mock import patch

import pymysql
from fastapi import HTTPException, UploadFile
from backend import ai, db, jobs, materials, system
from backend.security import store
from backend.security.context import use_context
from backend.storage import upload_path, write_bytes, read_bytes


@unittest.skipUnless(os.environ.get('RUN_JOBS_MYSQL_TESTS') == '1', 'opt-in MySQL integration test')
class JobsMySQLTests(unittest.TestCase):
    def test_tenant_bound_jobs_storage_cache_restart_and_revocation(self):
        previous = {key: os.environ.get(key) for key in ('MYSQL_DATABASE', 'ONTOLOGY_MYSQL_URL', 'ONTOLOGY_STORAGE_ROOT')}
        base = 'ontology_jobtest_' + secrets.token_hex(5)
        os.environ.pop('ONTOLOGY_MYSQL_URL', None)
        os.environ['MYSQL_DATABASE'] = base
        os.environ['ONTOLOGY_STORAGE_ROOT'] = tempfile.mkdtemp(prefix='ontology-job-storage-')
        original_candidates = materials.online_candidates
        password='Fixture9-'+secrets.token_hex(16)
        admin=None
        created=[]
        def principal(name):
            raw=store.login(name,password,'127.0.0.1','queue-test')
            return store.authenticate_session(raw,'test')[0]
        def fast(job):
            with store.control_connection() as c,c.cursor() as cur:
                cur.execute('UPDATE ontology_task_queue SET available_at=UTC_TIMESTAMP(6) WHERE job_id=%s',(job,))
        def fixture(p,content):
            with use_context(p):
                path=upload_path('same.txt',create_parent=True);write_bytes(path,content.encode())
                with closing(db.connect()) as c,c:
                    c.execute('INSERT INTO entities VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',('M-SAME','material','同名原件','测试','数据治理',p.tenant_id,'draft','user',json.dumps({'original_text':content,'stored_name':'same.txt','segmentation_status':'confirmed','candidates':[]},ensure_ascii=False),'2026-01-01','2026-01-01',1))
                    c.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?,?)',('M-SAME::1','M-SAME',1,'正文',content,'第1段','available'))
                    c.execute('INSERT INTO entities VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',('D-SAME','data','口径说明','测试','数据治理',p.tenant_id,'published','user',json.dumps({'definition':content,'content':content},ensure_ascii=False),'2026-01-01','2026-01-01',1))
        def queue(p,jid,mode='local'):
            with use_context(p),closing(db.connect()) as c,c:
                c.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)',(jid,'material_extract','M-SAME','queued',0,'排队','{}',db.now(),db.now()))
                jobs.enqueue(jid,'material_extract','M-SAME',['M-SAME',jid,mode,False])
            fast(jid)
        async def test():
            nonlocal admin
            store.initialize_control();store.bootstrap_superadmin('job-admin',password)
            token=store.login('job-admin',password,'127.0.0.1','queue-test');admin=store.authenticate_session(token,'test')[0]
            newtoken=store.change_password(admin,password,password+'x','127.0.0.1','queue-test');admin=store.authenticate_session(newtoken,'test')[0]
            identities=[]
            for i,tid in enumerate(('dept-01','dept-02')):
                db.ensure_tenant_schema(tid);created.append(store.get_tenant(tid)['db_name'])
                uid=store.create_user(admin,username='job-user'+str(i),display_name='Queue Test',tenant_id=tid,role='modeler',password=password,must_change_password=False)
                identities.append((uid,principal('job-user'+str(i))))
            (uid_a,a),(uid_b,b)=identities
            fixture(a,'甲机构应当保留甲方验收资料，字段含义由甲方确认。')
            fixture(b,'乙机构应当保留乙方合同资料，字段含义由乙方确认。')
            # Reproduce separate API workers reaching the same upload id concurrently.
            # A losing transaction may clean only its own file, never the winner's original.
            for different in (False, True):
                upload_token = 'concurrent-' + secrets.token_hex(12)
                uploads = [b'first upload content', b'different content' if different else b'first upload content']
                barrier = threading.Barrier(2)
                original_write = materials.write_bytes
                def synchronized_write(*args, **kwargs):
                    barrier.wait(timeout=10)
                    return original_write(*args, **kwargs)
                def upload(content):
                    with use_context(a):
                        try:
                            return asyncio.run(materials.upload_material(
                                UploadFile(filename='concurrent.txt', file=io.BytesIO(content)),
                                org_id=a.tenant_id, domain='并发测试', project_id='', task_id='',
                                matter_id='', upload_token=upload_token))
                        except HTTPException as error:
                            return error
                with patch.object(materials, 'write_bytes', synchronized_write):
                    results = await asyncio.gather(*(asyncio.to_thread(upload, content) for content in uploads))
                successful = [item for item in results if isinstance(item, dict)]
                assert len(successful) == (1 if different else 2), results
                if different:
                    assert next(item for item in results if isinstance(item, HTTPException)).status_code == 409
                else:
                    assert sum(bool(item.get('already_uploaded')) for item in successful) == 1
                entity = successful[0]['entity']
                with use_context(a):
                    original = upload_path(entity['payload']['stored_name'], must_exist=True)
                    assert hashlib.sha256(read_bytes(original)).hexdigest() == entity['payload']['sha256']
                    assert len(list(original.parent.glob(entity['id'] + '-*'))) == 1
            with use_context(a):
                assert read_bytes(upload_path('same.txt')).decode().startswith('甲机构')
                hit=ai.retrieve('口径说明',limit=5);assert any('甲机构' in x['_text'] for x in hit)
                assert all('乙机构' not in x['_text'] for x in hit)
            with use_context(b):
                assert read_bytes(upload_path('same.txt')).decode().startswith('乙机构')
                hit=ai.retrieve('口径说明',limit=5);assert any('乙机构' in x['_text'] for x in hit)
                assert all('甲机构' not in x['_text'] for x in hit)
            queue(a,'JOB-A');queue(b,'JOB-B')
            await asyncio.gather(jobs.run_one(),jobs.run_one())
            await jobs.run_one()
            for p,jid,forbidden in [(a,'JOB-A','乙机构'),(b,'JOB-B','甲机构')]:
                with use_context(p),closing(db.connect()) as c:
                    q=jobs.queue_status(jid);assert q['status']=='completed',q
                    row=c.execute('SELECT payload FROM entities WHERE id=?',('M-SAME',)).fetchone()
                    payload=json.loads(row['payload']);assert payload['candidates'];assert forbidden not in json.dumps(payload,ensure_ascii=False)
            # Runtime configuration and backup include only the captured tenant.
            for p,marker,other in [(a,'甲机构','乙机构'),(b,'乙机构','甲机构')]:
                with use_context(p):
                    synthetic_key='fixture-'+p.tenant_id+'-secret'
                    write_bytes(ai.config_path(create=True),json.dumps({'api_key':synthetic_key,'model':'fixture-model','base_url':ai.DEFAULT_BASE_URL,'revision':secrets.token_hex(16),'verified':False}).encode())
                    assert ai.config()['api_key']==synthetic_key
                    response=system.backup()
                    with zipfile.ZipFile(response.path) as archive:
                        names=archive.namelist();assert not any('runtime' in x for x in names),names
                        data=archive.read('data/mysql-workspace.json').decode()
                        assert marker in data and other not in data
                        assert archive.read('data/uploads/same.txt').decode().startswith(marker)
                    await response.background()
            # A leased task retains its original workspace after its session switches elsewhere.
            store.switch_workspace(admin,'dept-01')
            admin_a=store.authenticate_session(newtoken,'test')[0]
            queue(admin_a,'JOB-FIXED-WORKSPACE')
            store.switch_workspace(admin_a,'dept-02')
            admin_b=store.authenticate_session(newtoken,'test')[0]
            try:
                store.assert_principal_current(admin_a)
                raise AssertionError('HTTP stale workspace was accepted')
            except Exception as e:
                assert getattr(e,'status_code',None)==401,e
            await jobs.run_one()
            with use_context(a):
                assert jobs.queue_status('JOB-FIXED-WORKSPACE')['status']=='completed'
            with use_context(b):
                assert jobs.queue_status('JOB-FIXED-WORKSPACE') is None
            store.switch_workspace(admin_b,'hq');admin=store.authenticate_session(newtoken,'test')[0]
            # Durable duplicate job ID is a no-op, and completed jobs do not rerun.
            with use_context(a):
                assert jobs.enqueue('JOB-A','material_extract','M-SAME',['M-SAME','JOB-A','local',False])['already_queued']
            assert not await jobs.run_one()
            # A queued task loses authority before execution and never reaches business writes.
            queue(b,'JOB-REVOKED')
            store.update_user(admin,uid_b,{'is_active':False})
            await jobs.run_one()
            with store.control_connection() as c,c.cursor() as cur:
                cur.execute("SELECT status FROM ontology_task_queue WHERE job_id='JOB-REVOKED'");assert cur.fetchone()['status']=='cancelled'
            # Crash recovery: abandon a running lease, then claim the same persisted job again.
            queue(a,'JOB-RESUME')
            claimed=jobs._claim();assert claimed['job_id']=='JOB-RESUME'
            with store.control_connection() as c,c.cursor() as cur:
                cur.execute("UPDATE ontology_task_queue SET lease_until=DATE_SUB(UTC_TIMESTAMP(6),INTERVAL 1 SECOND) WHERE job_id='JOB-RESUME'")
            await jobs.run_one()
            with use_context(a):
                q=jobs.queue_status('JOB-RESUME');assert q['status']=='completed' and q['attempts']==2,q
            # Revocation during remote work: returning results may not commit.
            entered=asyncio.Event();release=asyncio.Event()
            async def controlled(*args):
                entered.set();await release.wait()
                return [],{}
            materials.online_candidates=controlled
            queue(a,'JOB-INFLIGHT','online')
            run=asyncio.create_task(jobs.run_one());await asyncio.wait_for(entered.wait(),10)
            store.update_user(admin,uid_a,{'is_active':False});release.set();await run
            with store.control_connection() as c,c.cursor() as cur:
                cur.execute("SELECT status FROM ontology_task_queue WHERE job_id='JOB-INFLIGHT'");assert cur.fetchone()['status']=='cancelled'
        try:
            asyncio.run(test())
        finally:
            materials.online_candidates = original_candidates
            config = db.mysql_config()
            with pymysql.connect(host=config['host'], port=config['port'], user=config['user'], password=config['password']) as connection, connection.cursor() as cursor:
                for name in [*created, base + '_control']:
                    cursor.execute('DROP DATABASE IF EXISTS `' + name + '`')
            shutil.rmtree(os.environ['ONTOLOGY_STORAGE_ROOT'], ignore_errors=True)
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


if __name__ == '__main__':
    unittest.main()
