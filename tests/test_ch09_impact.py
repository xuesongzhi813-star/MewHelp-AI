from dataclasses import replace

import pytest

from tests.test_ch09_versions import baseline


async def setup(tmp_path):
    from app.evaluation.contracts import ActiveRelease
    from app.evaluation.impact import ImpactService
    store,version=baseline(tmp_path)
    active=[ActiveRelease('release-a',version.version,version.labels_hash,version.split_hash,'corpus-a','profile-a')]
    reviews={7:{'id':7,'normalized_question':'MH-SH299袖长是多少？','approved_answer':'MH-SH299袖长为60厘米。','review_status':'通过'}}
    class Gateway:
        calls=[]
        async def suggest_label_impact(self,messages,budget):
            self.calls.append(messages)
            return {'affected':False,'reason':'合成模型漏报，由人工检查补正','changes':{}}
    gateway=Gateway()
    service=ImpactService(store,active=lambda:active[0],reviews=lambda ids:[reviews[i] for i in ids],gateway=gateway)
    draft=await service.prepare_impact([7],base=active[0])
    return store,version,active,reviews,service,draft,gateway


def edit_for(draft,case_id='out_of_scope-01'):
    return {'case_id':case_id,'changes':{'answerable':True,'ground_truth_answer':'袖长60厘米。',
        'required_facts':['60厘米'],'relevant_chunk_refs':[draft.reviews[0]['chunks'][0]['ref']]},
        'reason':'人工核对新核准规格，纠正模型漏报'}


async def test_full_scan_confirmation_and_new_version_keep_original_case_identity(tmp_path):
    store,version,active,reviews,service,draft,gateway=await setup(tmp_path)
    assert len(gateway.calls)==96
    edit=edit_for(draft)
    remaining=[c.id for c in version.cases if c.id!=edit['case_id']]
    confirmed=service.confirm_impact(draft.digest,edits=[edit],unchanged_case_ids=remaining,confirmed_by='审核样例')
    assert service.confirm_impact(draft.digest,edits=[edit],unchanged_case_ids=remaining,confirmed_by='审核样例')==confirmed
    new=store.freeze(version.version,draft,confirmed)
    assert new.version!=version.version and new.labels_hash!=version.labels_hash
    old=next(c for c in version.cases if c.id==edit['case_id'])
    current=next(c for c in new.cases if c.id==old.id)
    assert current.raw_query==old.raw_query and current.bucket==old.bucket and current.split==old.split
    assert new.calibration_ids==version.calibration_ids and new.test_ids==version.test_ids
    assert current.answerable and not old.answerable
    assert store.load(version.version)==version


async def test_unconfirmed_or_incomplete_confirmation_cannot_freeze(tmp_path):
    store,version,_,_,service,draft,_=await setup(tmp_path)
    with pytest.raises(ValueError):store.freeze(version.version,draft,None)
    with pytest.raises(ValueError,match='coverage'):
        service.confirm_impact(draft.digest,edits=[],unchanged_case_ids=[],confirmed_by='reviewer')
    with pytest.raises(ValueError):
        service.confirm_impact(draft.digest,edits=[],unchanged_case_ids=[c.id for c in version.cases],confirmed_by='')


async def test_changed_approved_content_or_active_release_invalidates_confirmation(tmp_path):
    from app.evaluation.impact import ImpactConflict
    _,version,active,reviews,service,draft,_=await setup(tmp_path)
    reviews[7]['approved_answer']='袖长61厘米。'
    with pytest.raises(ImpactConflict):
        service.confirm_impact(draft.digest,edits=[],unchanged_case_ids=[c.id for c in version.cases],confirmed_by='reviewer')
    reviews[7]['approved_answer']='MH-SH299袖长为60厘米。'
    active[0]=replace(active[0],release_id='changed')
    with pytest.raises(ImpactConflict):
        service.confirm_impact(draft.digest,edits=[],unchanged_case_ids=[c.id for c in version.cases],confirmed_by='reviewer')


async def test_no_label_or_binding_change_reuses_version(tmp_path):
    store,version,_,_,service,draft,_=await setup(tmp_path)
    confirmed=service.confirm_impact(draft.digest,edits=[],unchanged_case_ids=[c.id for c in version.cases],confirmed_by='reviewer')
    assert store.freeze(version.version,draft,confirmed).version==version.version


@pytest.mark.parametrize('same_reviewer',[True,False])
async def test_concurrent_confirmation_is_idempotent_or_conflicts_without_overwrite(tmp_path,monkeypatch,same_reviewer):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from app.evaluation.impact import ImpactConflict
    store,version,_,_,service,draft,_=await setup(tmp_path)
    original=store.artifacts.immutable_json
    barrier=threading.Barrier(2)
    def synchronized(path,data):
        if path.endswith('/confirmation.json'):barrier.wait(timeout=5)
        return original(path,data)
    monkeypatch.setattr(store.artifacts,'immutable_json',synchronized)
    edit=edit_for(draft)
    remaining=[c.id for c in version.cases if c.id!=edit['case_id']]
    def confirm(label):
        try:return service.confirm_impact(draft.digest,edits=[edit],unchanged_case_ids=remaining,confirmed_by=label)
        except ImpactConflict:return None
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes=list(executor.map(confirm,['reviewer-a','reviewer-a' if same_reviewer else 'reviewer-b']))
    saved=[item for item in outcomes if item is not None]
    assert len(saved)==(2 if same_reviewer else 1)
    if same_reviewer:assert saved[0]==saved[1]
    frozen=store.freeze(version.version,draft,saved[0])
    assert store.load(frozen.version).confirmation==saved[0].model_dump(mode='json')


async def test_edit_cannot_change_raw_question_split_or_invent_source(tmp_path):
    _,version,_,_,service,draft,_=await setup(tmp_path)
    for changes in [{'raw_query':'new'},{'split':'test'},{'relevant_chunk_refs':[{'kind':'review','review_id':999}]}]:
        edit={'case_id':version.cases[0].id,'changes':changes,'reason':'synthetic'}
        with pytest.raises(ValueError):
            service.confirm_impact(draft.digest,edits=[edit],unchanged_case_ids=[c.id for c in version.cases[1:]],confirmed_by='reviewer')


async def test_test_only_changes_do_not_change_calibration_projection(tmp_path):
    store,version,_,_,service,draft,_=await setup(tmp_path)
    case=next(c for c in version.cases if c.split=='test' and not c.answerable)
    edit=edit_for(draft,case.id)
    confirmed=service.confirm_impact(draft.digest,edits=[edit],unchanged_case_ids=[c.id for c in version.cases if c.id!=case.id],confirmed_by='reviewer')
    new=store.freeze(version.version,draft,confirmed)
    assert [c for c in new.cases if c.split=='calibration']==[c for c in version.cases if c.split=='calibration']


async def test_reverting_labels_creates_a_new_immutable_version(tmp_path):
    store,version,active,_,service,draft,_=await setup(tmp_path)
    edit=edit_for(draft)
    confirmed=service.confirm_impact(draft.digest,edits=[edit],unchanged_case_ids=[c.id for c in version.cases if c.id!=edit['case_id']],confirmed_by='reviewer')
    new=store.freeze(version.version,draft,confirmed)
    active[0]=replace(active[0],release_id='release-b',dataset_version=new.version,labels_hash=new.labels_hash)
    draft2=await service.prepare_impact([7],base=active[0])
    original=next(c for c in version.cases if c.id==edit['case_id'])
    from app.evaluation.versions import EDITABLE
    reverse={'case_id':original.id,'changes':{key:original.model_dump(mode='json')[key] for key in EDITABLE},'reason':'人工撤销不可靠新增事实'}
    confirmed2=service.confirm_impact(draft2.digest,edits=[reverse],unchanged_case_ids=[c.id for c in new.cases if c.id!=original.id],confirmed_by='reviewer')
    reverted=store.freeze(new.version,draft2,confirmed2)
    assert reverted.labels_hash==version.labels_hash and reverted.version not in {version.version,new.version}
    assert reverted.parent_version==new.version
