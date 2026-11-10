"""Pure-local CLI/oracle tests. No credentials, network, SDK run or Docker calls."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('harness_live_cli', Path(__file__).resolve().parents[1]/'scripts/check_harness_live.py')
cli = importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
KEY = 'SYNTHETIC_LOCAL_ONLY_NOT_A_PROVIDER_KEY'


def proposal(case_id='create', fee=0):
    value = cli.scenario(case_id)
    profile = cli.scenario('revise')['manifest']
    if fee:
        for row in profile['test_cases']:
            row['expected_output']['amount_cents'] += fee
    files = deepcopy(value['files']);files['scripts/main.py'] = cli.script('payload["amount_yuan"] * 100'+(' + 25' if fee else ''))
    return {'ok': True, 'proposal': {'summary': '合成提案', 'files': files, 'manifest': profile}}


def runner_pass(files, entrypoint, cases, **kwargs):
    return {'outcome': 'passed', 'report': {'cases': [{'status': 'passed'} for _ in cases]}}


class OracleTests(unittest.TestCase):
    def test_runner_receives_independent_oracle(self):
        case=cli.scenario('create');response=proposal()
        response['proposal']['manifest']['inputs']['title']='合法输入注解'
        response['proposal']['manifest']['outputs']['properties']['amount_cents']['description']='人民币分'
        with patch('backend.skill_execution.runner_status', return_value={'available': True}), patch('backend.skill_execution.execute_tests', side_effect=runner_pass) as runner:
            status,checks=cli.assess(case,response,KEY)
        self.assertEqual(status,'passed')
        fixed=runner.call_args.args[2]
        self.assertEqual([row['input']['amount_yuan'] for row in fixed],[0,37,90001])
        self.assertEqual([row['expected_output']['amount_cents'] for row in fixed],[0,3700,9000100])
        self.assertNotEqual(fixed,response['proposal']['manifest']['test_cases'])

    def test_model_invented_expected_values_never_become_oracle(self):
        response=proposal();response['proposal']['manifest']['test_cases'][0]['expected_output']['amount_cents']=999
        with patch('backend.skill_execution.execute_tests') as runner:
            status,_=cli.assess(cli.scenario('create'),response,KEY)
        self.assertEqual(status,'failed');runner.assert_not_called()

    def test_missing_runner_does_not_pass_script_semantics(self):
        with patch('backend.skill_execution.runner_status',return_value={'available':False}):
            status,checks=cli.assess(cli.scenario('create'),proposal(),KEY)
        self.assertEqual(status,'needs_manual_review')
        self.assertIn('isolated_script_behavior_unverified',[row['check'] for row in checks])

    def test_runner_failure_is_not_hidden_as_manual(self):
        with patch('backend.skill_execution.runner_status',return_value={'available':True}), patch('backend.skill_execution.execute_tests',return_value={'outcome':'failed','report':{'cases':[]}}):
            self.assertEqual(cli.assess(cli.scenario('create'),proposal(),KEY)[0],'failed')

    def test_fee_revision_uses_a_distinct_fixed_oracle(self):
        with patch('backend.skill_execution.runner_status',return_value={'available':True}),patch('backend.skill_execution.execute_tests',side_effect=runner_pass) as runner:
            self.assertEqual(cli.assess(cli.scenario('revise'),proposal('revise',25),KEY)[0],'passed')
        self.assertEqual(runner.call_args.args[2][0]['expected_output'],{'amount_cents':25})

    def test_ambiguity_and_refusal_language_require_review(self):
        for case_id in ('ambiguity','unauthorized'):
            case=cli.scenario(case_id)
            response={'proposal':{'summary':'不能仅凭这段文字判定正确拒绝。','files':case['files'],'manifest':case['manifest']}}
            self.assertEqual(cli.assess(case,response,KEY)[0],'needs_manual_review')

    def test_authority_mutation_key_echo_bad_package_fail_without_text_leak(self):
        for mutation in ('grant','key','path','syntax'):
            response=proposal()
            if mutation=='grant':response['proposal']['manifest']['dependencies']=[{'entity_id':'foreign','version':1,'kind':'data'}]
            if mutation=='key':response['proposal']['summary']=KEY
            if mutation=='path':response['proposal']['files']['../escape.py']='pass'
            if mutation=='syntax':response['proposal']['files']['scripts/main.py']='def broken('
            status,checks=cli.assess(cli.scenario('create'),response,KEY)
            self.assertEqual(status,'failed');self.assertNotIn(KEY,json.dumps(checks))

    def test_usage_only_copies_bounded_numeric_allowlist(self):
        result=cli.safe_usage({'model_responses':2,'complete':True,'input_tokens':10,'output_tokens':2,
            'cache_read_tokens':0,'cache_write_tokens':0,'api_key':KEY,'response':KEY})
        self.assertEqual(result,{'status':'reported','model_responses':2,'complete':True,'input_tokens':10,
            'output_tokens':2,'cache_read_tokens':0,'cache_write_tokens':0})
        self.assertEqual(cli.safe_usage(None)['status'],'unknown')
        partial=cli.safe_usage({'model_responses':1,'complete':False,'input_tokens':None})
        self.assertIsNone(partial['input_tokens']);self.assertEqual(partial['status'],'unknown')
        self.assertEqual(cli.safe_usage({'model_responses':1,'complete':True,'input_tokens':True})['status'],'unknown')

    def test_cli_rejects_key_arguments_without_echo_and_listing_never_reads_key(self):
        output=io.StringIO()
        with redirect_stderr(output),self.assertRaises(SystemExit):
            cli.main(['--api-key',KEY])
        self.assertNotIn(KEY,output.getvalue())
        with patch.object(cli,'read_key',side_effect=AssertionError('must not read')),redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(['--list-cases']),0)

    def test_key_is_taken_from_ephemeral_environment_and_removed(self):
        values={'DEEPSEEK_API_KEY':KEY}
        with patch.object(cli.os,'environ',values):self.assertEqual(cli.read_key(),KEY)
        self.assertNotIn('DEEPSEEK_API_KEY',values)


class RunTests(unittest.IsolatedAsyncioTestCase):
    async def test_serial_stop_after_first_failure_no_retry_and_no_exception_text(self):
        class Executor:
            calls=0
            def __init__(self,maximum):assert maximum==1
            async def execute(self,payload):
                Executor.calls+=1
                raise RuntimeError(KEY)
            async def close(self):pass
        with patch('backend.skill_harness.HarnessExecutor',Executor):
            result=await cli.run_cases(['create','revise'],KEY,'/synthetic/runtime')
        self.assertEqual(Executor.calls,1);self.assertEqual(len(result),1)
        self.assertEqual(result[0]['status'],'failed');self.assertNotIn(KEY,json.dumps(result))

    async def test_no_proposal_is_review_for_ambiguity_but_failure_for_creation(self):
        from fastapi import HTTPException
        class Executor:
            def __init__(self,maximum):pass
            async def execute(self,payload):raise HTTPException(422,{'code':'HARNESS_NO_PROPOSAL','message':KEY})
            async def close(self):pass
        with patch('backend.skill_harness.HarnessExecutor',Executor):
            review=await cli.run_cases(['ambiguity'],KEY,'/synthetic/runtime')
            creation=await cli.run_cases(['create'],KEY,'/synthetic/runtime')
        self.assertEqual(review[0]['status'],'needs_manual_review')
        self.assertEqual(creation[0]['status'],'failed');self.assertNotIn(KEY,json.dumps(review))


if __name__=='__main__':unittest.main()
