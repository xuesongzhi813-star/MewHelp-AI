import json
from types import SimpleNamespace
from pathlib import Path
import pytest
import httpx
from openai import AuthenticationError
from app.kb import embedding as emb


def test_embedding_auth_failure_is_safe_and_not_retried(monkeypatch):
    calls=[]
    def create(**kwargs):
        calls.append(1)
        raise AuthenticationError('private-secret-provider-body', response=httpx.Response(401, request=httpx.Request('POST','https://test.invalid')), body={})
    settings=SimpleNamespace(embedding_model='test',embedding_dim=1024)
    with pytest.raises(emb.EmbeddingError) as caught:
        emb._embed_batch(SimpleNamespace(embeddings=SimpleNamespace(create=create)),settings,['synthetic'])
    assert caught.value.kind=='authentication'
    assert 'private-secret' not in str(caught.value)
    assert len(calls)==1


def test_profile_names_differing_fields_without_values(tmp_path):
    from scripts.eval_retrieval import load_profile
    p=tmp_path/'profile.json'
    p.write_text(json.dumps({'split':'calibration','threshold':.25,'identity':{'runtime_corpus_hash':'old-private'}}))
    with pytest.raises(ValueError,match='runtime_corpus_hash') as caught:
        load_profile(p,{'runtime_corpus_hash':'new-private'})
    assert 'old-private' not in str(caught.value) and 'new-private' not in str(caught.value)


async def test_search_reports_embedding_dependency_not_migration(client,monkeypatch):
    from app.api import kb
    monkeypatch.setattr(kb,'get_settings',lambda:SimpleNamespace(kb_corpus_version='ch04'))
    def broken(*a,**kw): raise emb.EmbeddingError('private-secret')
    monkeypatch.setattr(kb,'retrieve_result',broken)
    response=await client.post('/api/kb/search',json={'query':'synthetic','route':'dense'})
    assert response.status_code==502
    assert '嵌入' in response.json()['detail'] and 'private-secret' not in response.text


async def test_workflow_reports_embedding_failure_safely(tmp_path):
    from app.workflow.runtime import WorkflowRuntime,open_checkpointer
    from app.history import InMemorySessionStore
    from tests.test_workflow_graph import Gateway
    def broken(*a,**kw): raise emb.EmbeddingError('private-secret')
    async with open_checkpointer(str(tmp_path/'failure.sqlite')) as saver:
        runtime=WorkflowRuntime(saver,InMemorySessionStore(10),gateway_factory=lambda model:model,retrieve=broken)
        events=[e async for e in runtime.stream_turn('s','退货',model=Gateway('商品咨询'))]
    assert events[-1]['type']=='error'
    assert '嵌入' in events[-1]['message'] and 'private-secret' not in str(events)


def test_demo_can_select_new_profile_without_overwriting_frozen_file(monkeypatch,tmp_path):
    from scripts import demo_ch05
    import os
    for key in ('CHAT_WORKFLOW_ENABLED','KB_CORPUS_VERSION','KNOWLEDGE_COLLECTION','HYBRID_COLLECTION',
                'KNOWLEDGE_STRATEGY','KB_MIGRATION_MANIFEST','RERANK_MIN_SCORE','WORKFLOW_CHECKPOINT_PATH'):
        monkeypatch.setenv(key, os.environ.get(key,''))
    path=tmp_path/'calibration.json'
    path.write_text(json.dumps({'threshold':.35}))
    monkeypatch.setenv('CH05_CALIBRATION_PROFILE',str(path))
    demo_ch05.configure()
    import os
    assert os.environ['RERANK_MIN_SCORE']=='0.35'

async def test_ingest_pending_explains_recovery_without_duplicate_submit(client,monkeypatch):
    from app.api import kb
    from app.kb.dualwrite import VectorizeResult
    monkeypatch.setattr(kb,'_existing_fingerprints',lambda:set())
    monkeypatch.setattr(kb,'_persist_chunks',lambda candidates:[91])
    monkeypatch.setattr(kb,'vectorize_pending',lambda:VectorizeResult(0,1,1))
    response=await client.post('/api/kb/ingest',json={'markdown':'# 合成测试\n\n合成正文','source_title':'测试','vectorize':True})
    assert response.status_code==502 and response.json()['inserted']==1
    assert '不要重复提交' in response.json()['detail']


def test_embedding_traceback_does_not_include_provider_body():
    import traceback
    def create(**kwargs):
        raise AuthenticationError('provider-private-body', response=httpx.Response(401,request=httpx.Request('POST','https://test.invalid')),body={})
    with pytest.raises(emb.EmbeddingError) as caught:
        emb._embed_batch(SimpleNamespace(embeddings=SimpleNamespace(create=create)),SimpleNamespace(embedding_model='test',embedding_dim=1024),['synthetic'])
    assert 'provider-private-body' not in ''.join(traceback.format_exception(caught.value))
