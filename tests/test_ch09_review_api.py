import pytest

from tests.test_ch09_pool import pool_session


def test_review_decisions_are_cas_and_idempotent(pool_session):
    from app.flywheel.publishing import ReviewRepository,ReviewConflict
    from app.models import ReviewQueue
    from sqlalchemy.orm import sessionmaker
    row=ReviewQueue(normalized_question='合成审核问题');pool_session.add(row);pool_session.flush()
    repo=ReviewRepository(session_factory=sessionmaker(bind=pool_session.connection(),join_transaction_mode='create_savepoint'))
    first=repo.decide(row.id,'通过',approved_answer='人工核准的合成答案')
    assert repo.decide(row.id,'通过',approved_answer='人工核准的合成答案')==first
    with pytest.raises(ReviewConflict):repo.decide(row.id,'驳回')
    with pytest.raises(ReviewConflict):repo.decide(row.id,'通过',approved_answer='不同答案')


def test_rejected_is_terminal_and_has_no_approved_answer(pool_session):
    from app.flywheel.publishing import ReviewRepository,ReviewConflict
    from app.models import ReviewQueue
    from sqlalchemy.orm import sessionmaker
    row=ReviewQueue(normalized_question='驳回合成项');pool_session.add(row);pool_session.flush()
    repo=ReviewRepository(session_factory=sessionmaker(bind=pool_session.connection(),join_transaction_mode='create_savepoint'))
    assert repo.decide(row.id,'驳回')['review_status']=='驳回'
    assert repo.decide(row.id,'驳回')['approved_answer'] is None
    with pytest.raises(ReviewConflict):repo.decide(row.id,'通过',approved_answer='不能重开')


async def test_review_http_contract_and_confirmation_before_publication(client):
    from app.main import app
    from app.api.review import get_publisher,get_repository
    calls=[]
    class Impact:
        def confirm_impact(self,digest,**kwargs):calls.append(('confirm',digest));return {'digest':digest}
    class Publisher:
        impact=Impact()
        async def preview(self,ids):return {'review_ids':ids,'suggestions':[]}
        async def publish(self,confirmation):calls.append(('publish',confirmation));return {'stage':'complete'}
    class Repo:
        def list(self,**kwargs):return [{'id':7,'normalized_question':'合成标准问题','occurrence_count':2}]
        def detail(self,id):return {'id':id,'events':[{'raw_question':'合成原话','snapshot':{'hits':[{'answer':'当时片段'}]}}]}
        def decide(self,id,status,**kwargs):return {'id':id,'review_status':status,**kwargs}
    app.dependency_overrides[get_publisher]=lambda:Publisher()
    app.dependency_overrides[get_repository]=lambda:Repo()
    assert (await client.get('/api/review')).json()['items'][0]['occurrence_count']==2
    assert (await client.get('/api/review/7')).json()['events'][0]['snapshot']['hits'][0]['answer']=='当时片段'
    response=await client.post('/api/review/7/approve',json={'approved_answer':'人工核准答案'})
    assert response.json()['publication_status']=='awaiting_impact_confirmation' and not calls
    assert (await client.post('/api/review/impact',json={'review_ids':[7]})).status_code==200
    response=await client.post('/api/review/impact/confirm',json={'digest':'a'*64,'edits':[],
        'unchanged_case_ids':['synthetic'],'confirmed_by':'reviewer'})
    assert response.json()['stage']=='complete' and [c[0] for c in calls]==['confirm','publish']
    assert (await client.post('/api/review/7/approve',json={'approved_answer':'ok','review_status':'驳回'})).status_code==422


async def test_old_kb_write_is_blocked_while_ch09_owner_is_online(client,monkeypatch):
    monkeypatch.setattr('app.flywheel.publishing.owner_present',lambda:True)
    called=[]
    monkeypatch.setattr('app.api.kb._persist_chunks',lambda chunks:called.append(chunks))
    response=await client.post('/api/kb/ingest',json={'markdown':'# 合成\n正文','source_title':'合成'})
    assert response.status_code==409 and not called


async def test_impact_view_exposes_original_labels_and_recent_release_status(client,tmp_path):
    from types import SimpleNamespace
    from app.main import app
    from app.api.review import get_publisher
    from tests.test_ch09_impact import setup
    store,version,_,_,impact,draft,_=await setup(tmp_path)
    class Publisher:
        versions=store
        async def preview(self,ids):return draft
        def list_releases(self,limit=20):return [{'release_id':'release-'+'a'*24,'stage':'failed','error_code':'synthetic'}]
    app.dependency_overrides[get_publisher]=lambda:Publisher()
    view=(await client.post('/api/review/impact',json={'review_ids':[7]})).json()
    assert len(view['cases'])==96
    assert view['cases'][0]['raw_query']==version.cases[0].raw_query
    assert len([c for c in view['cases'] if c['split']=='calibration'])==24
    response=await client.get('/api/review/releases')
    assert response.status_code==200 and response.json()['items'][0]['stage']=='failed'
