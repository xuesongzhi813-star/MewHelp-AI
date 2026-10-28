"""Real corpus snapshots, source binding and fixed-24 evidence calibration."""
from dataclasses import asdict
from pathlib import Path

from app.evaluation.artifacts import digest
from app.evaluation.versions import bind_version,review_binding_key,labels_hash
from app.kb import store,migration
from app.kb.calibration import CalibrationCase,calibrate
from app.kb.documents import chunk_fingerprint


def corpus_state():
    report=migration.verify_hybrid()
    if report['status']!='consistent':raise ValueError('corpus/vector consistency check failed')
    rows=list(store.iter_chunks_by_ids(migration.eligible_chunk_ids()))
    identity=[{key:getattr(row,key) for key in ('id','category','questions','answer','section_path',
        'content_type','vectorize_status')} for row in sorted(rows,key=lambda row:row.id)]
    return rows,digest(identity),{row.id for row in rows}


def retrieval_code_hash():
    root=Path(__file__).resolve().parents[2]
    import hashlib
    return digest({str(path.relative_to(root)):hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((root/'app/kb').glob('*.py'))})


def retrieval_runtime_hash():
    from app.config import get_settings
    settings=get_settings()
    # Credentials are intentionally excluded; provider addresses are only hashed.
    keys=('embedding_model','embedding_base_url','embedding_dim','rerank_model','rerank_base_url',
        'knowledge_strategy','knowledge_collection','hybrid_collection','kb_corpus_version','milvus_uri')
    return digest({key:getattr(settings,key) for key in keys})


def require_no_unfinished_release(versions):
    for path in versions.artifacts.root.glob('releases/*/manifest.json'):
        saved=versions.artifacts.read_json(str(path.relative_to(versions.artifacts.root)).replace('\\','/'))
        if saved.get('stage')!='complete':raise ValueError('unfinished publication must be resumed before calibration')


class KnowledgeWriter:
    def preflight(self):
        corpus_state()

    def persist(self,reviews,*,lease):
        from app.flywheel.publishing import guard_legacy_write
        guard_legacy_write()
        existing=list(store.iter_chunks_by_ids(migration.eligible_chunk_ids()))
        by_fp={}
        for row in existing:by_fp.setdefault(chunk_fingerprint(row.questions,row.answer),[]).append(row.id)
        bindings={}
        with store._session_scope(None) as session:
            for review in reviews:
                inserted=[]
                for item in review['chunks']:
                    ref=item['ref'];chunk=item['chunk'];matches=by_fp.get(ref['fingerprint'],[])
                    if len(matches)>1:raise ValueError('publication fingerprint ambiguous')
                    if matches:chunk_id=matches[0]
                    else:
                        if len(chunk['section_path'])>512:raise ValueError('knowledge source path too long')
                        candidate=store.ChunkCandidate(**{key:chunk[key] for key in
                            ('category','questions','answer','section_path','content_type','is_key_clause')})
                        chunk_id=store.insert_chunks([candidate],session=session)[0]
                        by_fp[ref['fingerprint']]=[chunk_id];inserted.append(chunk_id)
                    bindings[review_binding_key(ref)]=chunk_id
                # Do not alter sibling pointers or section provenance of reused chunks.
                store.link_siblings(inserted,session=session)
            session.flush();lease.assert_owned();guard_legacy_write()
        return bindings

    def vectorize(self,ids,*,boundary):
        from app.kb.dualwrite import vectorize_ids
        return vectorize_ids(ids,boundary=boundary)


class CalibrationService:
    async def __call__(self,version,bindings,reviews):
        import asyncio
        from app.kb.sources import get_sources
        from app.kb.query import build_understood_query_plan
        from app.kb.retriever import retrieve_result
        runtime_hash=retrieval_runtime_hash()
        rows,corpus_hash,vector_ids=await asyncio.to_thread(corpus_state)
        bound=bind_version(version,get_sources(corpus_version='ch04'),rows,
            review_contents=reviews,review_bindings=bindings,vector_ids=vector_ids)
        selected=[item for item in bound if item.case.split=='calibration']
        if len(selected)!=24 or {item.case.answerable for item in selected}!={True,False}:
            raise ValueError('24 calibration cases with both labels required')
        results={}
        for item in selected:
            case=item.case
            plan=build_understood_query_plan(case.raw_query).model_copy(update={'filters':case.filters})
            results[case.id]=await asyncio.to_thread(retrieve_result,case.raw_query,strategy='hybrid_rerank',plan=plan)
        _,after,_=await asyncio.to_thread(corpus_state)
        if after!=corpus_hash:raise ValueError('corpus changed during calibration')
        if retrieval_runtime_hash()!=runtime_hash:raise ValueError('retrieval configuration changed during calibration')
        identity={'runtime_corpus_hash':corpus_hash,'retrieval_code_hash':retrieval_code_hash(),
            'retrieval_runtime_hash':runtime_hash,
            'calibration_labels_hash':labels_hash([item.case for item in selected])}
        policy=calibrate([CalibrationCase(item.case.id,item.case.split,item.case.answerable,item.relevant_ids)
            for item in selected],results,identity=identity)
        return {**identity,'dataset_version':version.version,'labels_hash':version.labels_hash,
            'split_hash':version.split_hash,'policy':asdict(policy),'calibration_size':24}


def load_policy(versions,identity):
    from app.kb.evidence import EvidencePolicy
    payload=versions.artifacts.read_json('active.json')
    if payload['identity']!=asdict(identity):raise ValueError('active identity changed')
    profile=versions.artifacts.read_json(payload['profile_path'])
    if digest(profile)!=identity.profile_hash:raise ValueError('profile hash mismatch')
    if profile.get('retrieval_runtime_hash')!=retrieval_runtime_hash():
        raise ValueError('retrieval configuration changed; recalibrate required')
    if (profile['dataset_version'],profile['labels_hash'],profile['split_hash'])!=(
            identity.dataset_version,identity.labels_hash,identity.split_hash):raise ValueError('profile labels mismatch')
    return EvidencePolicy(**profile['policy'])


def validate_release(versions,identity):
    load_policy(versions,identity)
    payload=versions.artifacts.read_json('active.json')
    profile=versions.artifacts.read_json(payload['profile_path'])
    if profile['retrieval_code_hash']!=retrieval_code_hash():raise ValueError('retrieval code changed; recalibrate required')
    rows,corpus_hash,vector_ids=corpus_state()
    if corpus_hash!=identity.runtime_corpus_hash:raise ValueError('corpus drift; recalibrate required')
    from app.kb.sources import get_sources
    version=versions.load(identity.dataset_version)
    bind_version(version,get_sources(corpus_version='ch04'),rows,
        review_contents=payload.get('review_contents',[]),review_bindings=payload.get('review_bindings',{}),vector_ids=vector_ids)


async def install_profile(versions,version,profile,*,bindings,reviews,assert_owned=None):
    from app.evaluation.contracts import ActiveRelease
    from app.flywheel.worker import _db
    profile_hash=digest(profile);path=f'profiles/{profile_hash}.json'
    await _db(versions.artifacts.immutable_json,path,profile)
    identity=ActiveRelease('release-'+digest({'profile':profile_hash,'bindings':bindings})[:24],
        version.version,version.labels_hash,version.split_hash,profile['runtime_corpus_hash'],profile_hash)
    def activate():
        if assert_owned:assert_owned()
        versions.artifacts.write_json('active.json',{'identity':asdict(identity),'profile_path':path,
            'review_bindings':bindings,'review_contents':reviews})
    await _db(activate)
    return identity


async def bootstrap(versions,calibrator=None,*,assert_owned=None):
    from app.flywheel.worker import _db
    from app.flywheel.publishing import load_active
    require_no_unfinished_release(versions)
    if assert_owned:await _db(assert_owned)
    if versions.artifacts.exists('active.json'):return load_active(versions)
    version=await _db(versions.bootstrap,Path('data/eval/ch04'))
    profile=await (calibrator or CalibrationService())(version,{},[])
    require_no_unfinished_release(versions)
    return await install_profile(versions,version,profile,bindings={},reviews=[],assert_owned=assert_owned)


async def recalibrate_current(versions,calibrator=None,*,assert_owned=None):
    from app.flywheel.worker import _db
    require_no_unfinished_release(versions)
    if assert_owned:await _db(assert_owned)
    old=versions.artifacts.read_json('active.json')
    version=versions.load(old['identity']['dataset_version'])
    profile=await (calibrator or CalibrationService())(version,old.get('review_bindings',{}),old.get('review_contents',[]))
    if versions.artifacts.read_json('active.json')!=old:raise ValueError('active changed during calibration')
    require_no_unfinished_release(versions)
    return await install_profile(versions,version,profile,bindings=old.get('review_bindings',{}),reviews=old.get('review_contents',[]),assert_owned=assert_owned)
