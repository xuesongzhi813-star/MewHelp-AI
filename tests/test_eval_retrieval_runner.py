import pytest
from app.kb import evaluation_judge as judge
from app.kb.contracts import Evidence, Hit


def evidence():
    return [Evidence(1, Hit(1, .9, '商品', '规格', 'MH-DJ101胸围110厘米。', '文档章节', '规格 > MH-DJ101'))]


class Model:
    def __init__(self, response):
        self.response, self.calls = response, []
    async def ainvoke(self, messages):
        self.calls.append(messages)
        return self.response


@pytest.fixture(autouse=True)
def isolate_runtime_snapshot(monkeypatch):
    from scripts import eval_retrieval as runner
    original = runner._runtime_corpus_identity
    monkeypatch.setattr(runner, '_runtime_corpus_identity', lambda: 'isolated-corpus')
    return original


def test_runtime_snapshot_binds_actual_rows_and_requires_consistency(isolate_runtime_snapshot, monkeypatch):
    from dataclasses import replace
    from app.kb import migration, store
    from types import SimpleNamespace
    row = store.StoredChunk(1,'商品','问题','原文','章节','文档章节',False,'done','1',None,None)
    rows = [row]
    monkeypatch.setattr(migration, 'load_manifest', lambda: SimpleNamespace(phase='validated',target_collection='isolated'))
    monkeypatch.setattr(migration, 'eligible_chunk_ids', lambda: {r.id for r in rows})
    monkeypatch.setattr(store, 'iter_chunks_by_ids', lambda ids: iter(rows))
    monkeypatch.setattr(migration, 'verify_hybrid', lambda: {'status':'consistent'})
    before = isolate_runtime_snapshot()
    rows[0] = replace(row, answer='新原文')
    assert isolate_runtime_snapshot() != before
    rows[0] = replace(row, id=2)
    assert isolate_runtime_snapshot() != before
    monkeypatch.setattr(migration, 'verify_hybrid', lambda: {'status':'mismatch'})
    with pytest.raises(ValueError, match='一致'):
        isolate_runtime_snapshot()


def test_run_rejects_changed_corpus_before_publishing_report(runner_env, monkeypatch, tmp_path):
    runner, _, _ = runner_env
    values = iter(['initial', 'changed'])
    monkeypatch.setattr(runner, '_runtime_corpus_identity', lambda: next(values))
    with pytest.raises(ValueError, match='语料'):
        runner.run_evaluation(split='test',mode='compare',output_dir=tmp_path/'out',cache_dir=tmp_path/'cache')
    assert not (tmp_path/'out/summary.json').exists()


async def test_judge_counts_supported_facts_and_refusal_is_na():
    model = Model({'facts': [
        {'text':'胸围110厘米','supported':True,'evidence_numbers':[1],'reason':'原文明确'},
        {'text':'保证合身','supported':False,'evidence_numbers':[],'reason':'没有支持'},
    ]})
    result = await judge.judge_faithfulness('尺寸', '胸围110厘米且保证合身。[1]', evidence(), model=model)
    assert result.score == .5 and result.supported_count == 1 and result.fact_count == 2
    assert (await judge.judge_faithfulness('尺寸', '', evidence(), model=model)).score is None
    assert len(model.calls) == 1


async def test_judge_bad_citation_or_empty_fact_list_is_failure():
    for response in ({'facts':[]}, {'facts':[{'text':'事实','supported':True,'evidence_numbers':[2],'reason':'引用未知'}]}):
        result = await judge.judge_faithfulness('尺寸', '实质性回答。[1]', evidence(), model=Model(response))
        assert result.error and result.score is None


async def test_required_facts_coverage_is_separate_and_validates_count():
    model = Model({'covered':[True, False]})
    assert await judge.judge_required_facts('胸围110厘米', ['110厘米','纯棉'], model=model) == [True,False]
    with pytest.raises(judge.JudgeServiceError):
        await judge.judge_required_facts('答案', ['事实'], model=model)


async def test_judge_timeout_is_failed_not_zero_or_one(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    monkeypatch.setattr(judge, 'get_settings', lambda: SimpleNamespace(answer_timeout=.01))
    class Slow:
        async def ainvoke(self, messages):
            await asyncio.Event().wait()
    result = await judge.judge_faithfulness('问题', '答案。[1]', evidence(), model=Slow())
    assert result.error and result.score is None


@pytest.fixture
def runner_env(monkeypatch):
    from scripts import eval_retrieval as runner
    from app.kb import evaluation as ev
    from app.kb.contracts import QueryPlan, RetrievalResult
    calls = {'plan':0, 'embed':0, 'retrieve':[]}
    from app.config import get_settings
    settings = get_settings().model_copy(update={'kb_corpus_version':'ch04'})
    monkeypatch.setattr(runner, 'get_settings', lambda: settings)
    case = ev.load_cases()[0]
    monkeypatch.setattr(runner, '_load_bound_cases', lambda split: [ev.BoundCase(case, {1}, ev.load_eval_manifest()['corpus_hash'])])
    def plan(raw, **kwargs):
        calls['plan'] += 1
        return QueryPlan(raw_query=raw, normalized_query=raw, lexical_query=raw, filters=kwargs['filters'])
    def embed(texts):
        calls['embed'] += 1
        return [[.1] * 1024]
    def retrieve(query, **kwargs):
        calls['retrieve'].append(kwargs)
        return RetrievalResult(kwargs['plan'], kwargs['strategy'], (evidence()[0].hit,), (1,))
    answer = Model({'can_answer':True, 'reason':'证据充分', 'answer':'MH-DJ101胸围110厘米。[1]', 'used_citation_numbers':[1]})
    faithful = Model({'facts':[{'text':'胸围110厘米','supported':True,'evidence_numbers':[1],'reason':'有依据'}]})
    required = Model({'covered':[True]})
    monkeypatch.setattr(runner, 'build_query_plan', plan)
    monkeypatch.setattr(runner, 'embed_texts', embed)
    monkeypatch.setattr(runner, 'retrieve_result', retrieve)
    monkeypatch.setattr(runner, '_models', lambda: (answer, faithful, required))
    return runner, calls, answer


def test_runner_reuses_one_plan_and_embedding_for_four_strategies_and_cache(runner_env, tmp_path):
    runner, calls, answer = runner_env
    kwargs = dict(split='test', mode='compare', output_dir=tmp_path / 'report', cache_dir=tmp_path / 'cache')
    report = runner.run_evaluation(**kwargs)
    assert calls['plan'] == calls['embed'] == 1
    assert len(calls['retrieve']) == 4 and len(answer.calls) == 4
    vectors = [c['query_vector'] for c in calls['retrieve'] if c['strategy'] != 'bm25']
    assert all(v is vectors[0] for v in vectors)
    assert len(report['rows']) == 4 and (tmp_path / 'report/summary.md').is_file()
    again = runner.run_evaluation(**kwargs)
    assert len(answer.calls) == 4 and all(row['cache_hit'] for row in again['rows'])


def test_bm25_only_runner_does_not_embed(runner_env, tmp_path):
    runner, calls, _ = runner_env
    runner.run_evaluation(split='test', mode='compare', strategies=['bm25'], output_dir=tmp_path / 'out', cache_dir=tmp_path / 'cache')
    assert calls['embed'] == 0


def test_shared_embedding_failure_keeps_bm25_and_writes_failed_rows(runner_env, monkeypatch, tmp_path):
    runner, calls, _ = runner_env
    def failed(texts):
        raise TimeoutError('synthetic embedding failure')
    monkeypatch.setattr(runner, 'embed_texts', failed)
    report = runner.run_evaluation(split='test', mode='compare', output_dir=tmp_path/'out', cache_dir=tmp_path/'cache')
    assert len(report['rows']) == 4
    assert [r['error'] for r in report['rows']] == ['TimeoutError', None, 'TimeoutError', 'TimeoutError']
    assert [c['strategy'] for c in calls['retrieve']] == ['bm25']
    assert (tmp_path/'out/details.jsonl').is_file()


def test_calibrate_rejects_test_split_and_freezes_all_candidates(tmp_path):
    from scripts import eval_retrieval as runner
    path = tmp_path / 'profile.json'
    rows = [{'answerable':False,'can_answer':True,'max_rerank_score':.2,'split':'calibration','strategy':'hybrid_rerank'},
            {'answerable':True,'can_answer':True,'max_rerank_score':.8,'split':'calibration','strategy':'hybrid_rerank'}]
    with pytest.raises(ValueError, match='calibration'):
        runner.calibrate_profile(rows, split='test', output_path=path)
    assert not path.exists()
    profile = runner.calibrate_profile(rows, split='calibration', output_path=path)
    assert profile['threshold'] == .5 and len(profile['candidates']) == 17
    assert profile['candidates'][0]['answerable_denominator'] == 1


def test_calibration_cannot_relabel_test_rows_as_calibration(tmp_path):
    from scripts import eval_retrieval as runner
    with pytest.raises(ValueError, match='calibration'):
        runner.calibrate_profile([{'answerable':False,'can_answer':False,'max_rerank_score':.1,
            'split':'test','strategy':'hybrid_rerank'}], split='calibration', output_path=tmp_path/'profile.json')


def test_runner_rejects_legacy_corpus_before_models(runner_env, monkeypatch, tmp_path):
    runner, calls, _ = runner_env
    settings = runner.get_settings().model_copy(update={'kb_corpus_version':'ch03'})
    monkeypatch.setattr(runner, 'get_settings', lambda: settings)
    with pytest.raises(ValueError, match='ch04'):
        runner.run_evaluation(split='test', mode='compare', output_dir=tmp_path/'out', cache_dir=tmp_path/'cache')
    assert calls['plan'] == 0


def test_deployment_rejects_mismatched_profile_before_retrieval(runner_env, tmp_path):
    import json
    runner, calls, _ = runner_env
    path = tmp_path / 'profile.json'
    identity = runner._identity()
    identity['prompt_hash'] = 'different'
    path.write_text(json.dumps({'split':'calibration','identity':identity,'threshold':.5}), encoding='utf-8')
    with pytest.raises(ValueError, match='hash'):
        runner.run_evaluation(split='test', mode='deployment', output_dir=tmp_path/'out', cache_dir=tmp_path/'cache', profile=path)
    assert calls['retrieve'] == []


def test_runner_judge_failure_is_reported_and_not_cached(runner_env, monkeypatch, tmp_path):
    runner, _, answer = runner_env
    monkeypatch.setattr(runner, '_models', lambda: (answer, Model({'facts':[]}), Model({'covered':[True]})))
    report = runner.run_evaluation(split='test', mode='compare', strategies=['bm25'], output_dir=tmp_path/'out', cache_dir=tmp_path/'cache')
    stats = report['summary']['overall']['bm25']
    assert stats['judge_failures'] == 1 and stats['faithfulness'] is None
    assert not list((tmp_path/'cache').glob('*.json'))
