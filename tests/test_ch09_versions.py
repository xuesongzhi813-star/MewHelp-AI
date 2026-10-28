import hashlib
import json
from pathlib import Path


def test_immutable_artifact_atomic_create_never_overwrites_concurrent_winner(tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from app.evaluation.artifacts import ArtifactStore
    store=ArtifactStore(tmp_path/'atomic')
    barrier=threading.Barrier(2)
    exists=store.exists
    def synchronized(relative):
        observed=exists(relative)
        barrier.wait(timeout=5)
        return observed
    monkeypatch.setattr(store,'exists',synchronized)
    def write(choice):
        try:store.immutable_json('shared.json',{'choice':choice});return 'saved'
        except ValueError:return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes=list(executor.map(write,['first','second']))
    assert sorted(outcomes)==['conflict','saved']
    assert store.read_json('shared.json')['choice'] in {'first','second'}

import pytest

CH04=Path('data/eval/ch04')


def baseline(tmp_path):
    from app.evaluation.versions import VersionStore
    store=VersionStore(tmp_path/'private')
    return store,store.bootstrap(CH04)


def test_bootstrap_is_immutable_and_keeps_all_fixed_ids_and_splits(tmp_path):
    before={name:hashlib.sha256((CH04/name).read_bytes()).hexdigest() for name in ['cases.jsonl','split.json','ANNOTATION_NOTES.md']}
    store,version=baseline(tmp_path)
    assert len(version.cases)==96 and len(version.calibration_ids)==24 and len(version.test_ids)==72
    assert set(version.calibration_ids).isdisjoint(version.test_ids)
    assert store.bootstrap(CH04)==version and store.load(version.version)==version
    assert before=={name:hashlib.sha256((CH04/name).read_bytes()).hexdigest() for name in before}


@pytest.mark.parametrize('path',['../escape.json','eval_versions/../../escape.json','/absolute.json','a\\..\\escape.json'])
def test_artifacts_reject_path_escape(tmp_path,path):
    from app.evaluation.artifacts import ArtifactStore
    with pytest.raises(ValueError):ArtifactStore(tmp_path).write_json(path,{'x':1})


def test_artifact_hash_tampering_and_invalid_version_fail_closed(tmp_path):
    from app.evaluation.artifacts import ArtifactStore
    store,version=baseline(tmp_path)
    with pytest.raises(ValueError):store.load('../bad')
    path=tmp_path/'private'/'eval_versions'/version.version/'dataset.json'
    content=json.loads(path.read_text(encoding='utf-8'))
    content['payload']['cases'][0]['raw_query']='tampered'
    path.write_text(json.dumps(content),encoding='utf-8')
    with pytest.raises(ValueError,match='hash'):store.load(version.version)


def test_unknown_source_ref_cannot_become_a_valid_case(tmp_path):
    from app.evaluation.contracts import VersionedCase
    _,version=baseline(tmp_path)
    data=version.cases[0].model_dump()
    data['relevant_chunk_refs']=[{'kind':'unapproved','fingerprint':'a'*64}]
    with pytest.raises(ValueError):VersionedCase.model_validate(data)


def test_review_chunk_plan_is_deterministic_and_reuses_build_chunks():
    from app.evaluation.impact import review_knowledge
    row={'id':7,'normalized_question':'真丝衣服怎么洗？','approved_answer':'请送专业干洗。','review_status':'通过'}
    first=review_knowledge(row)
    assert first==review_knowledge(row) and first['chunks']
    assert all(c['ref']['approved_content_hash']==first['content_hash'] for c in first['chunks'])
    assert first['chunks'][0]['ref']['review_id']==7


def test_source_binding_reuses_existing_chunk_without_rewriting_its_original_source(tmp_path):
    from app.evaluation.versions import bind_version,review_binding_key
    from app.evaluation.impact import review_knowledge
    from app.evaluation.contracts import ReviewSourceRef
    from tests.test_kb_evaluation import corpus
    store,version=baseline(tmp_path)
    sources,rows=corpus()
    row=rows[0]
    review=review_knowledge({'id':7,'normalized_question':row.questions,'approved_answer':row.answer,'review_status':'通过'})
    ref=ReviewSourceRef.model_validate(review['chunks'][0]['ref'])
    case=version.cases[0].model_copy(update={'relevant_chunk_refs':[*version.cases[0].relevant_chunk_refs,ref]})
    synthetic=version.model_copy(update={'cases':(case,*version.cases[1:])})
    before=row.section_path
    bound=bind_version(synthetic,sources,rows,review_contents=[review],
        review_bindings={review_binding_key(ref):row.id},vector_ids={r.id for r in rows})
    assert row.id in bound[0].relevant_ids and row.section_path==before
    with pytest.raises(ValueError):
        bind_version(synthetic,sources,rows,review_contents=[review],review_bindings={},vector_ids={r.id for r in rows})
    with pytest.raises(ValueError):
        bind_version(synthetic,sources,rows,review_contents=[review],
            review_bindings={review_binding_key(ref):row.id},vector_ids=set())


def test_atomic_write_retries_transient_permission_denial_without_losing_old_file(tmp_path,monkeypatch):
    import os
    from app.evaluation.artifacts import ArtifactStore
    store=ArtifactStore(tmp_path);store.write_json('state.json',{'value':'old'})
    original=os.replace;attempts=[]
    def transient(source,target):
        attempts.append(1)
        if len(attempts)<3:
            assert store.read_json('state.json')=={'value':'old'}
            raise PermissionError('synthetic sharing denial')
        return original(source,target)
    monkeypatch.setattr(os,'replace',transient)
    store.write_json('state.json',{'value':'new'})
    assert len(attempts)==3 and store.read_json('state.json')=={'value':'new'}


def test_legacy_migration_manifest_also_preserves_old_file_on_transient_denial(tmp_path,monkeypatch):
    from app.kb import migration
    path=tmp_path/'manifest.json';path.write_text('old',encoding='utf-8')
    monkeypatch.setattr(migration,'_path',lambda:path)
    original=migration.os.replace;attempts=[]
    def replace(source,target):
        attempts.append(1)
        if len(attempts)<3:
            assert path.read_text(encoding='utf-8')=='old'
            raise PermissionError('synthetic transient denial')
        return original(source,target)
    monkeypatch.setattr(migration.os,'replace',replace)
    migration._save(migration.MigrationManifest(target_collection='synthetic',source_hashes={},
        legacy_snapshot_ids=[],reused_ids=[],legacy_excluded_ids=[]))
    assert len(attempts)==3 and 'synthetic' in path.read_text(encoding='utf-8')
