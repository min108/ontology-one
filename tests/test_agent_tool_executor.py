"""Real disposable process ownership and private session-tool framing tests."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from backend.agent_tool_executor import AgentToolExecutor
from backend.mcp_contracts import ToolFailure
from backend.security.context import Principal
from backend.agent_tool_worker import _hydrate_ledger
from fastapi import HTTPException


def principal():
    return Principal(user_id='synthetic-user',username='synthetic',home_tenant_id='tenant-1',tenant_id='tenant-1',
                     role='modeler',is_superadmin=False,session_id='synthetic-session',auth_version=1,
                     database_name='synthetic_unused',request_id='outer-request',identity_kind='session')


CHILD = r'''
import json,sys,time
request=json.loads(sys.stdin.buffer.readline())
if sys.argv[1]=='hold':time.sleep(30)
print(json.dumps({'ready':True,'request_id':request['request_id']}),flush=True)
assert json.loads(sys.stdin.buffer.readline())=={'finalize':True}
result={'ok':True,'request_id':request['request_id'],'value':{'tenant':request['principal']['tenant_id']},
        'receipts':[],'skill_versions':{},'group_versions':{},'asset_versions':{}}
if request['tool']=='__validate_ledger':result['value']={'valid':True}
if sys.argv[1]=='cross':
    result['receipts']=[{'purpose':'query','tenant_id':'another-tenant','source_id':'source','mapping_id':'mapping',
                        'mapping_version':1,'source_version':1,'schema_hash':'a'*64}]
elif sys.argv[1]=='stale':
    result={'ok':False,'request_id':request['request_id'],'error':{'code':'STALE_MAPPING','message':'版本已变化','retryable':False}}
print(json.dumps(result,ensure_ascii=False),flush=True)
'''


class ProbeExecutor(AgentToolExecutor):
    def __init__(self, mode='ok'):
        super().__init__(1);self.mode=mode;self.spawned=[];self.started=asyncio.Event()
    async def _spawn(self):
        process=await self._spawn_command([sys.executable,'-X','utf8','-B','-c',CHILD,self.mode])
        self.spawned.append(process);self.started.set();return process


class LedgerContractTests(unittest.TestCase):
    def ledger(self):
        return {'receipts':[{'purpose':'query','tenant_id':'tenant-1','source_id':'source','mapping_id':'mapping',
                             'mapping_version':1,'source_version':1,'schema_hash':'a'*64}],
                'skill_versions':{'skill':[1,'b'*64]},'group_versions':{'tenant-2/asset':2},'asset_versions':{'local-asset':3}}

    def test_ledger_hydration_preserves_only_bounded_metadata_and_skill_tuple(self):
        bridge=SimpleNamespace();value=self.ledger()
        _hydrate_ledger(bridge,value,principal(),['mapping'],['skill'])
        self.assertEqual(bridge.skill_versions,{'skill':(1,'b'*64)})
        self.assertEqual(bridge.asset_versions,{'local-asset':3})

    def test_cross_tenant_or_unselected_mapping_proofs_are_rejected(self):
        for scope in ('tenant','mapping'):
            value=self.ledger();value['receipts'][0]['tenant_id' if scope=='tenant' else 'mapping_id']='other'
            with self.subTest(scope=scope),self.assertRaises(HTTPException):
                _hydrate_ledger(SimpleNamespace(),value,principal(),['mapping'],['skill'])

    def test_unselected_skills_non_integer_versions_and_row_payloads_are_rejected(self):
        values=[]
        value=self.ledger();value['skill_versions']={'not-selected':[1,'b'*64]};values.append(value)
        value=self.ledger();value['asset_versions']={'local-asset':True};values.append(value)
        value=self.ledger();value['group_versions']={'tenant-2/asset':0};values.append(value)
        value=self.ledger();value['rows']=[{'business':'should-not-be-in-proof-ledger'}];values.append(value)
        for index,value in enumerate(values):
            with self.subTest(index=index),self.assertRaises(ValueError):
                _hydrate_ledger(SimpleNamespace(),value,principal(),['mapping'],['skill'])


class AgentToolProcessTests(unittest.IsolatedAsyncioTestCase):
    async def invoke(self, executor, seconds=2):
        return await executor.execute(principal(),['mapping'],[], 'ontology_discover',{},'tool-request',
                                      deadline=asyncio.get_running_loop().time()+seconds)
    def reaped(self,executor):
        self.assertEqual(executor.active,0);self.assertFalse(executor.calls);self.assertFalse(executor.children)
        self.assertTrue(all(process.returncode is not None for process in executor.spawned))
    async def test_actual_two_phase_session_worker_retains_fixed_identity(self):
        executor=ProbeExecutor()
        self.assertEqual((await self.invoke(executor))['value'],{'tenant':'tenant-1'})
        self.reaped(executor)
    async def test_foreign_tenant_receipt_is_never_returned(self):
        executor=ProbeExecutor('cross')
        with self.assertRaises(ToolFailure) as caught:await self.invoke(executor)
        self.assertEqual(caught.exception.code,'FORBIDDEN');self.reaped(executor)
    async def test_finalize_stale_mapping_keeps_typed_error_and_discards_value(self):
        executor=ProbeExecutor('stale')
        with self.assertRaises(ToolFailure) as caught:await self.invoke(executor)
        self.assertEqual(caught.exception.code,'STALE_MAPPING');self.reaped(executor)
    async def test_timeout_kills_owned_worker_before_slot_release(self):
        executor=ProbeExecutor('hold')
        with self.assertRaises(ToolFailure) as caught:await self.invoke(executor,.15)
        self.assertEqual(caught.exception.code,'TIMEOUT');self.reaped(executor)
    async def test_cancel_reaps_owned_worker_and_closes_admission_cleanly(self):
        executor=ProbeExecutor('hold');task=asyncio.create_task(self.invoke(executor,30))
        await executor.started.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.reaped(executor);await executor.close();self.assertTrue(executor.closing)
    async def test_private_ledger_revalidation_is_killable_and_reaps_before_success(self):
        executor=ProbeExecutor()
        self.assertTrue(await executor.validate(principal(),[],[],[],{},{},{},
                            deadline=asyncio.get_running_loop().time()+2))
        self.reaped(executor)

    async def test_application_identity_is_rejected_before_process_creation(self):
        executor=ProbeExecutor()
        with self.assertRaises(ToolFailure):
            await executor.execute(replace(principal(),identity_kind='application'),[],[], 'ontology_discover',{},'tool-request',
                                   deadline=asyncio.get_running_loop().time()+1)
        self.assertEqual(executor.spawned,[])
    async def test_model_environment_credentials_are_not_inherited_by_source_worker(self):
        executor=AgentToolExecutor(1)
        with patch.dict(os.environ,{'DEEPSEEK_API_KEY':'synthetic-secret','MYSQL_PASSWORD':'synthetic-db'}), \
             patch.object(executor,'_spawn_command',new_callable=AsyncMock) as spawn:
            await executor._spawn()
        command,environment=spawn.call_args.args
        self.assertNotIn('DEEPSEEK_API_KEY',environment)
        self.assertEqual(environment['MYSQL_PASSWORD'],'synthetic-db')
        self.assertEqual(command[-1],'backend.agent_tool_worker')


if __name__=='__main__':unittest.main()
