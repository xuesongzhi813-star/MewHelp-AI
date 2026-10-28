from contextlib import contextmanager

import pytest

from tests.test_ch09_versions import baseline
from tests.test_ch09_pool import pool_session


class Repository:
    def __init__(self):self.rows=[]
    def recover_interrupted(self):
        for row in self.rows:
            if row['metrics']['status']=='running':row['metrics']['status']='interrupted'
    def find_slot(self,slot):return next((r for r in self.rows if r['metrics'].get('slot')==slot),None) if slot else None
    def start(self,trigger,size,metrics,*,lease=None):
        self.rows.append({'id':len(self.rows)+1,'triggered_by':trigger,'dataset_size':size,'metrics':metrics})
        return self.rows[-1]['id']
    def finish(self,id,metrics):self.rows[id-1]['metrics']=metrics


@contextmanager
def lock(*args,**kwargs):
    class Lease:
        def assert_owned(self):pass
    yield Lease()


async def fixture(tmp_path,*,changed=False,service_error=False):
    from app.evaluation.runner import EvaluationRunner,case_metrics
    from app.evaluation.contracts import ActiveRelease
    from app.evaluation.versions import BoundVersionedCase
    from app.flywheel.publishing import ReleaseCoordinator
    from tests.test_workflow_knowledge import result
    from dataclasses import replace
    store,version=baseline(tmp_path)
    active=[ActiveRelease('a',version.version,version.labels_hash,version.split_hash,'corpus','profile')]
    called=[]
    async def execute(bound,identity,run_id):
        called.append(bound.case.id)
        if changed:active[0]=replace(active[0],runtime_corpus_hash='changed')
        return case_metrics(bound,result(.9),can_answer=bound.case.answerable,
            faithfulness={'score':1.} if bound.case.answerable else None,
            error='synthetic_service_error' if service_error else None)
    repo=Repository()
    runner=EvaluationRunner(store,ReleaseCoordinator(active=lambda:active[0]),execute,repository=repo,
        lock_factory=lock,binding_loader=lambda dataset,identity:[BoundVersionedCase(c,frozenset({1}) if c.answerable else frozenset()) for c in dataset.cases])
    return runner,repo,called,version


async def test_72_cases_one_run_one_row_and_denominators(tmp_path):
    runner,repo,called,version=await fixture(tmp_path)
    result=await runner.run_evaluation(trigger='手动')
    assert result.status=='success' and len(repo.rows)==1 and repo.rows[0]['dataset_size']==72
    assert set(called)==set(version.test_ids) and not set(called)&set(version.calibration_ids)
    scores=repo.rows[0]['metrics']['scores']
    assert scores['retrieval_denominator']==60 and scores['faithfulness_denominator']==60
    assert scores['correct_refusal_rate']==1 and scores['coverage_denominator']==72


async def test_version_change_invalidates_whole_round(tmp_path):
    runner,repo,_,_=await fixture(tmp_path,changed=True)
    result=await runner.run_evaluation(trigger='手动')
    assert result.status=='invalid' and repo.rows[0]['metrics']['status']=='invalid'


async def test_core_service_errors_do_not_become_zero_score_success(tmp_path):
    runner,repo,_,_=await fixture(tmp_path,service_error=True)
    result=await runner.run_evaluation(trigger='手动')
    assert result.status=='failed' and repo.rows[0]['metrics']['scores']['faithfulness'] is None


async def test_slot_is_idempotent_and_old_running_recovers(tmp_path):
    runner,repo,called,_=await fixture(tmp_path)
    repo.start('手动',72,{'status':'running','run_id':'lost'})
    first=await runner.run_evaluation(trigger='定时',slot='2026-10-27T18:00:00+00:00')
    second=await runner.run_evaluation(trigger='定时',slot='2026-10-27T18:00:00+00:00')
    assert first.id==second.id and len(called)==72 and len(repo.rows)==2
    assert repo.rows[0]['metrics']['status']=='interrupted'


def test_unanswerable_and_refusal_are_na_and_judge_failure_separate(tmp_path):
    from app.evaluation.runner import case_metrics
    from app.evaluation.versions import BoundVersionedCase
    from tests.test_workflow_knowledge import result
    _,version=baseline(tmp_path)
    negative=next(c for c in version.cases if not c.answerable)
    row=case_metrics(BoundVersionedCase(negative,frozenset()),result(.1),can_answer=False,faithfulness={'score':1.})
    assert row['recall_at_10'] is None and row['mrr_at_10'] is None and row['faithfulness'] is None
    positive=version.cases[0]
    row=case_metrics(BoundVersionedCase(positive,frozenset({1})),result(.9),can_answer=True,
        faithfulness={'error':'judge unavailable'})
    assert row['judge_error'] and row['faithfulness'] is None


async def test_default_judge_adapter_satisfies_json_mode_transport_contract(monkeypatch):
    from app.evaluation.executor import CaseExecutor
    from app.workflow.models import ModelGateway
    from app.kb.contracts import Evidence
    from tests.test_workflow_knowledge import result
    calls=[]
    async def provider(self,schema,messages,budget,**kwargs):
        text='\n'.join(m.content for m in messages)
        if 'json' not in text.lower() or 'evidence_numbers' not in text:
            raise ValueError('provider HTTP400: JSON mode requires explicit JSON instructions')
        calls.append(text)
        return schema.model_validate({'facts':[{'text':'合成事实','supported':True,'evidence_numbers':[1],'reason':'原文支持'}]})
    monkeypatch.setattr(ModelGateway,'_json',provider)
    executor=CaseExecutor(None)
    score=await executor._judge('合成问题','合成事实',[Evidence(1,result(.9).hits[0])])
    assert score['score']==1 and score['error'] is None and len(calls)==1


def test_trends_only_compare_matching_versions_and_protocols():
    from app.evaluation.runner import comparison_key,build_trends
    base={'status':'success','dataset_version':'a','labels_hash':'l','split_hash':'s',
        'metric_protocol_version':'v1','judge_model':'m','judge_prompt_hash':'p','scores':{'recall_at_10':.9},'finished_at':'1'}
    altered={**base,'dataset_version':'b','scores':{'recall_at_10':.1},'finished_at':'2'}
    same={**base,'scores':{'recall_at_10':.8},'finished_at':'3'}
    result=build_trends([{'id':i+1,'metrics':m} for i,m in enumerate([base,altered,same])])
    assert result[1]['delta']['recall_at_10'] is None
    assert result[2]['delta']['recall_at_10']==pytest.approx(-.1)
    assert comparison_key(base)!=comparison_key(altered)


async def test_real_graph_executor_uses_isolated_state_and_never_records_pool(tmp_path,monkeypatch):
    from app.evaluation.executor import CaseExecutor
    from app.evaluation.contracts import ActiveRelease
    from app.evaluation.versions import BoundVersionedCase
    from app.flywheel.publishing import ReleaseCoordinator
    from app.kb.evidence import EvidencePolicy
    from tests.test_ch09_knowledge_flow import CheckedGateway
    from tests.test_workflow_knowledge import result
    _,version=baseline(tmp_path)
    from app.kb.contracts import RetrievalFilters
    case=version.cases[0].model_copy(update={'filters':RetrievalFilters(category='售后政策')})
    identity=ActiveRelease('a',version.version,version.labels_hash,version.split_hash,'corpus','profile')
    coordinator=ReleaseCoordinator(active=lambda:identity,
        policy_loader=lambda value:EvidencePolicy('test',.5,(.8,.1,.1),.6,'test'))
    def forbidden(*args,**kwargs):raise AssertionError('real persistence invoked')
    monkeypatch.setattr('app.kb.low_confidence.record_low_confidence',forbidden)
    monkeypatch.setattr('app.context.store.ConversationStore.create_conversation',forbidden)
    gateway=CheckedGateway()
    async def judge(question,answer,evidence):return {'score':1.}
    retrieved=[]
    def retrieve(question,**kwargs):retrieved.append(kwargs['plan']);return result(.9)
    executor=CaseExecutor(coordinator,work_root=tmp_path/'eval',model_factory=lambda:object(),
        gateway_factory=lambda _:gateway,service_factory=lambda:None,retrieve=retrieve,judge=judge)
    await executor.start()
    try:
        row=await executor(BoundVersionedCase(case,frozenset({1})),identity,'run')
        assert not row.get('service_error') and gateway.answers==1 and row['faithfulness']==1
        assert retrieved[0].filters.category=='售后政策'
        assert 'agent_decide' in row['node_trace'] and 'agent_answer' in row['node_trace']
        gateway.decision=False;gateway.checks.clear();gateway.answers=0
        row=await executor(BoundVersionedCase(case,frozenset({1})),identity,'run-negative')
        assert row['can_answer'] is False and not row.get('service_error') and row['faithfulness'] is None
    finally:await executor.aclose()


async def test_readonly_tool_service_hard_stops_write_calls():
    from app.evaluation.executor import ReadOnlyToolService,SideEffectAttempt
    from app.tooling.policy import PolicyLoader
    service=ReadOnlyToolService(PolicyLoader('config/tool_policy.json'))
    with pytest.raises(SideEffectAttempt):await service.execute('create_ticket',{'description':'never write'})


def test_eval_repository_updates_the_same_real_row_with_outer_rollback(pool_session):
    from app.evaluation.runner import EvalRepository
    from app.models import EvalRun
    from app.flywheel.locks import mysql_lock
    from sqlalchemy.orm import sessionmaker
    import uuid
    repo=EvalRepository(session_factory=sessionmaker(bind=pool_session.connection(),join_transaction_mode='create_savepoint'))
    with mysql_lock('ch09-eval-test-'+uuid.uuid4().hex,timeout=0) as lease:
        id=repo.start('手动',72,{'run_id':uuid.uuid4().hex,'status':'running'},lease=lease)
    repo.finish(id,{'status':'success','scores':{'recall_at_10':.9}})
    row=pool_session.get(EvalRun,id)
    assert row.dataset_size==72 and row.triggered_by=='手动' and row.metrics['status']=='success'


async def test_evaluation_api_manual_trigger_runs_and_trends(client):
    from app.main import app
    from app.api.evaluations import get_runner
    from app.evaluation.runner import EvalRunResult
    calls=[]
    class Repo:
        def list(self,limit):return [{'id':7,'metrics':{'status':'success','scores':{'faithfulness':.9},
            'dataset_version':'fixed','labels_hash':'labels','split_hash':'split','finished_at':'2026-10-27T00:00:00+00:00'}}]
    class Runner:
        repository=Repo()
        async def run_evaluation(self,**kwargs):calls.append(kwargs);return EvalRunResult(7,'success',{})
    app.dependency_overrides[get_runner]=lambda:Runner()
    response=await client.post('/api/evaluations/run',json={'trigger':'手动'})
    assert response.status_code==200 and response.json()['id']==7 and calls==[{'trigger':'手动'}]
    assert (await client.get('/api/evaluations/runs')).json()['items'][0]['id']==7
    assert (await client.get('/api/evaluations/trends')).json()['points'][0]['values']['faithfulness']==.9
    assert (await client.post('/api/evaluations/run',json={'trigger':'定时','slot':'forged'})).status_code==422
