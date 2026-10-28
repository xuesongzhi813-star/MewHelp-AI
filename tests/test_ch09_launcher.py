def test_ch09_configuration_keeps_ch08_paths_independent(monkeypatch):
    import os
    from scripts import demo_ch09
    monkeypatch.setattr('scripts.demo_ch08.configure',lambda **kwargs:None)
    monkeypatch.setattr(demo_ch09,'verify_local_target',lambda:None)
    keys=['CH09_ENABLED','FLYWHEEL_ENABLED','WORKFLOW_CHECKPOINT_PATH','CH09_DATA_ROOT']
    before={key:os.environ.get(key) for key in keys}
    demo_ch09.configure(require_active=False)
    from app.config import get_settings
    settings=get_settings()
    assert settings.ch09_enabled and settings.flywheel_enabled
    assert settings.workflow_checkpoint_path.replace('\\','/').endswith('log/ch09/checkpoints.sqlite')
    assert 'ch08' not in settings.ch09_data_root
    # Configuration is process-local; test must not affect later unrelated fixtures.
    for key,value in before.items():
        if value is None:os.environ.pop(key,None)
        else:os.environ[key]=value
    get_settings.cache_clear()


async def test_bootstrap_calibration_failure_never_creates_active(tmp_path):
    import pytest
    from app.evaluation.release import bootstrap
    from app.evaluation.versions import VersionStore
    versions=VersionStore(tmp_path/'private')
    async def failed(version,bindings,reviews):
        assert len(version.calibration_ids)==24 and len(version.test_ids)==72
        raise ValueError('synthetic calibration failure')
    with pytest.raises(ValueError):await bootstrap(versions,failed)
    assert not versions.artifacts.exists('active.json')


def test_serve_port_conflict_is_explicit_without_killing_existing_listener():
    import socket
    import pytest
    from scripts.demo_ch09 import check_port
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));listener.listen()
        port=listener.getsockname()[1]
        with pytest.raises(OSError):check_port(port)
        assert listener.getsockname()[1]==port


def test_evaluation_cli_accepts_explicit_no_cache_for_real_rounds(monkeypatch,capsys):
    from scripts import eval_ch09
    from app.evaluation.runner import EvalRunResult
    monkeypatch.setattr('scripts.demo_ch09.configure',lambda **kwargs:None)
    monkeypatch.setattr('app.flywheel.publishing.owner_present',lambda:False)
    calls=[]
    async def offline(trigger,slot):
        calls.append((trigger,slot))
        return EvalRunResult(123,'success',{})
    monkeypatch.setattr(eval_ch09,'offline',offline)
    assert eval_ch09.main(['--trigger','手动','--no-cache'])==0
    assert calls==[('手动',None)]


async def test_application_owner_yields_the_live_lease_for_runtime_validation(monkeypatch):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from app.flywheel import publishing
    calls=[]
    lease=SimpleNamespace(assert_owned=lambda:calls.append('validated'))
    @asynccontextmanager
    async def scoped(name):
        assert name==publishing.OWNER_LOCK
        yield lease
    monkeypatch.setattr(publishing,'async_lock_scope',scoped)
    assert publishing._owner_lease is None
    async with publishing.application_owner() as owner:
        assert owner is lease
        owner.assert_owned()
        assert publishing._owner_lease is lease
    assert publishing._owner_lease is None
    assert calls==['validated']


async def test_calibration_adapter_only_queries_24_and_does_not_use_test_labels(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from app.evaluation import release
    from tests.test_ch09_versions import baseline
    from tests.test_workflow_knowledge import result
    _,version=baseline(tmp_path)
    queries=[]
    by_question={case.raw_query:case for case in version.cases}
    monkeypatch.setattr(release,'corpus_state',lambda:([],'fixed-corpus',set()))
    monkeypatch.setattr(release,'bind_version',lambda version,*a,**k:[SimpleNamespace(case=c,
        relevant_ids=frozenset({1}) if c.answerable else frozenset()) for c in version.cases])
    def retrieve(question,**kwargs):
        queries.append(question)
        return result(.9 if by_question[question].answerable else .1)
    monkeypatch.setattr('app.kb.retriever.retrieve_result',retrieve)
    first=await release.CalibrationService()(version,{},[])
    assert len(queries)==24 and {by_question[q].id for q in queries}==set(version.calibration_ids)
    test=next(c for c in version.cases if c.split=='test')
    changed=test.model_copy(update={'forbidden_claims':[*test.forbidden_claims,'仅测试标签变化']})
    altered=version.model_copy(update={'cases':tuple(changed if c.id==test.id else c for c in version.cases)})
    second=await release.CalibrationService()(altered,{},[])
    assert first['policy']==second['policy']


import pytest

@pytest.mark.parametrize('field,value',[
    ('rerank_model','other-synthetic-reranker'),('rerank_base_url','https://other.invalid/v1'),
    ('embedding_model','other-synthetic-embedding'),('embedding_base_url','https://other.invalid/v1'),
    ('embedding_dim',2048),('milvus_uri','http://127.0.0.1:19999')])
async def test_model_configuration_change_invalidates_calibrated_profile(tmp_path,monkeypatch,field,value):
    from types import SimpleNamespace
    from app.evaluation import release
    from app.config import get_settings
    from tests.test_workflow_knowledge import result
    from tests.test_ch09_versions import baseline
    store,version=baseline(tmp_path)
    monkeypatch.setattr(release,'corpus_state',lambda:([],'fixed-corpus',set()))
    monkeypatch.setattr(release,'bind_version',lambda dataset,*a,**k:[SimpleNamespace(case=c,
        relevant_ids=frozenset({1}) if c.answerable else frozenset()) for c in dataset.cases])
    monkeypatch.setattr('app.kb.retriever.retrieve_result',lambda *a,**k:result(.9))
    profile=await release.CalibrationService()(version,{},[])
    identity=await release.install_profile(store,version,profile,bindings={},reviews=[])
    release.validate_release(store,identity)
    initial=get_settings()
    monkeypatch.setattr('app.config.get_settings',lambda:initial.model_copy(update={field:value}))
    import pytest
    with pytest.raises(ValueError,match='configuration'):
        release.validate_release(store,identity)


async def test_offline_recalibration_cannot_supersede_unfinished_publication(tmp_path):
    import pytest
    from app.evaluation.release import recalibrate_current
    from app.evaluation.versions import VersionStore
    store=VersionStore(tmp_path/'private')
    active={'identity':{'dataset_version':'synthetic-old'},'profile_path':'profiles/old.json'}
    store.artifacts.write_json('active.json',active)
    store.artifacts.write_json('releases/release-pending/manifest.json',{'stage':'done','write_started':True})
    called=[]
    class Unexpected(Exception):pass
    def load(*args):called.append('old-labels');raise Unexpected()
    store.load=load
    with pytest.raises(ValueError,match='unfinished'):
        await recalibrate_current(store)
    assert not called and store.artifacts.read_json('active.json')==active


async def test_lost_calibration_lease_cannot_install_active_after_long_model_work(tmp_path):
    from app.evaluation.release import recalibrate_current,install_profile
    from tests.test_ch09_versions import baseline
    from app.flywheel.locks import LockUnavailable
    store,version=baseline(tmp_path)
    def profile(corpus):return {'runtime_corpus_hash':corpus,'dataset_version':version.version,
        'labels_hash':version.labels_hash,'split_hash':version.split_hash,'policy':{'synthetic':True}}
    await install_profile(store,version,profile('old-corpus'),bindings={},reviews=[])
    before=store.artifacts.read_json('active.json');checks=[]
    async def calibrator(*args):return profile('new-corpus')
    def assert_owned():
        checks.append(True)
        if len(checks)>1:raise LockUnavailable('synthetic lease loss after calibration')
    import pytest
    with pytest.raises(LockUnavailable):
        await recalibrate_current(store,calibrator,assert_owned=assert_owned)
    assert len(checks)==2 and store.artifacts.read_json('active.json')==before
