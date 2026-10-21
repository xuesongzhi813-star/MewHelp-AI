"""真实MySQL事务 + 临时清单 + 假Milvus；绝不动生产集合。"""
from dataclasses import asdict
import json
from types import SimpleNamespace
import pytest

from app.config import get_settings
from app.kb import migration, store, dualwrite
from app.kb.documents import Source, build_chunks
from tests.test_kb_store import kb_tx  # noqa: F401 - reuse rollback fixture


@pytest.fixture
def setup_migration(kb_tx, tmp_path, monkeypatch):
    path = tmp_path / 'doc.md'
    path.write_text('# 迁移样例\n\n## MH-DJ101\n\n胸围110厘米。', encoding='utf-8')
    source = Source(path, '迁移样例', '文档章节')
    settings = get_settings().model_copy(update={
        'kb_migration_manifest': str(tmp_path / 'manifest.json'),
        'hybrid_collection': 'ch04_fake', 'embedding_batch_size': 2,
    })
    monkeypatch.setattr(migration, 'get_settings', lambda: settings)
    monkeypatch.setattr(migration, 'get_sources', lambda **kwargs: [source])
    collections = {}
    flushed = []
    client = SimpleNamespace(has_collection=lambda n: n in collections,
                             flush=lambda n: flushed.append(n))
    monkeypatch.setattr(migration.milvus, 'get_client', lambda: client)
    monkeypatch.setattr(migration.milvus, 'ensure_hybrid_collection',
                        lambda n: collections.setdefault(n, {}))
    def upsert(records, *, collection_name):
        collections[collection_name].update({r['id']: dict(r) for r in records})
    monkeypatch.setattr(migration.milvus, 'upsert_records', upsert)
    monkeypatch.setattr(migration.milvus, 'iter_records',
                        lambda n: iter(collections[n].values()))
    monkeypatch.setattr(migration, 'embed_texts', lambda texts: [[.01] * 1024 for _ in texts])
    return SimpleNamespace(settings=settings, source=source, collections=collections,
                           client=client, flushed=flushed)


def test_existing_done_rows_are_backfilled_into_empty_hybrid_collection(setup_migration):
    env = setup_migration
    chunk = build_chunks(env.source.path.read_text(encoding='utf-8'), env.source)[0]
    candidate = store.ChunkCandidate(chunk.category, chunk.questions, chunk.answer,
                                     chunk.section_path, chunk.content_type, chunk.is_key_clause)
    row_id = store.insert_chunks([candidate])[0]
    store.mark_done([row_id])
    before = asdict(store.fetch_by_ids([row_id])[row_id])
    result = migration.migrate_hybrid()
    assert result['status'] == 'consistent'
    assert str(row_id) in env.collections['ch04_fake']
    assert asdict(store.fetch_by_ids([row_id])[row_id]) == before
    manifest = migration.load_manifest()
    assert row_id in manifest.reused_ids and row_id not in manifest.legacy_excluded_ids
    assert migration.migrate_hybrid()['status'] == 'consistent'


def test_resume_keeps_original_snapshot_after_flush_failure(setup_migration):
    env = setup_migration
    before_ids = set(store.all_chunk_ids())
    def fail(name):
        raise RuntimeError('flush interrupted')
    env.client.flush = fail
    with pytest.raises(RuntimeError, match='interrupted'):
        migration.migrate_hybrid()
    manifest = migration.load_manifest()
    assert set(manifest.legacy_snapshot_ids) == before_ids
    new_ids = set(store.all_chunk_ids()) - before_ids
    assert new_ids and all(r.vectorize_status == 'pending' for r in store.fetch_by_ids(new_ids).values())
    env.client.flush = lambda name: None
    assert migration.migrate_hybrid()['status'] == 'consistent'
    assert set(migration.load_manifest().legacy_snapshot_ids) == before_ids
    assert new_ids.issubset(migration.eligible_chunk_ids())
    assert before_ids.isdisjoint(map(int, env.collections['ch04_fake']))


def test_missing_manifest_with_existing_target_fails_closed(setup_migration):
    env = setup_migration
    env.collections['ch04_fake'] = {}
    before = set(store.all_chunk_ids())
    with pytest.raises(ValueError, match='清单'):
        migration.migrate_hybrid()
    assert set(store.all_chunk_ids()) == before


def test_dry_run_does_not_create_manifest_or_collection(setup_migration):
    env = setup_migration
    report = migration.prepare_migration(dry_run=True)
    assert report['target_collection'] == 'ch04_fake'
    assert env.collections == {}
    from pathlib import Path
    assert not Path(env.settings.kb_migration_manifest).exists()


def test_tampered_source_or_wrong_target_cannot_rebase_manifest(setup_migration):
    env = setup_migration
    migration.migrate_hybrid()
    env.source.path.write_text('修改后的新语料', encoding='utf-8')
    with pytest.raises(ValueError, match='hash'):
        migration.load_manifest()


def test_verify_detects_same_count_but_stale_text(setup_migration):
    env = setup_migration
    migration.migrate_hybrid()
    record = next(iter(env.collections['ch04_fake'].values()))
    record['text'] = '过期正文'
    assert migration.verify_hybrid()['status'] == 'mismatch'


def _activate(monkeypatch, env):
    from app.kb import ingest
    settings = env.settings.model_copy(update={'kb_corpus_version': 'ch04'})
    monkeypatch.setattr(dualwrite, 'get_settings', lambda: settings)
    monkeypatch.setattr(ingest, 'get_settings', lambda: settings, raising=False)
    return ingest


def test_normal_vectorize_scopes_new_rows_and_never_touches_legacy(setup_migration, monkeypatch):
    env = setup_migration
    migration.migrate_hybrid()
    excluded = set(migration.load_manifest().legacy_excluded_ids)
    before = store.fetch_by_ids(excluded)
    row_id = store.insert_chunks([store.ChunkCandidate('售后', '新问题', '新回答', '人工', '人工录入', False)])[0]
    _activate(monkeypatch, env)
    monkeypatch.setattr(migration.milvus, 'ensure_collection', lambda: pytest.fail('访问旧集合'))
    result = dualwrite.vectorize_pending()
    assert (result.processed, result.failed, result.pending_left) == (1, 0, 0)
    assert str(row_id) in env.collections['ch04_fake']
    assert store.fetch_by_ids(excluded) == before


def test_ch04_reset_rejected_even_when_confirmed(setup_migration, monkeypatch):
    _activate(monkeypatch, setup_migration)
    before = store.all_chunk_ids()
    with pytest.raises(ValueError, match='ch04'):
        dualwrite.reset_all()
    assert store.all_chunk_ids() == before


def test_ch04_rebuild_does_not_drop_any_collection_or_reset_old_rows(setup_migration, monkeypatch):
    env = setup_migration
    migration.migrate_hybrid()
    excluded = migration.load_manifest().legacy_excluded_ids
    before = store.fetch_by_ids(excluded)
    _activate(monkeypatch, env)
    monkeypatch.setattr(migration.milvus, 'drop', lambda: pytest.fail('删除旧集合'))
    monkeypatch.setattr(store, 'reset_all_pending', lambda: pytest.fail('重置全库'))
    result = dualwrite.rebuild_all()
    assert result.processed == len(migration.eligible_chunk_ids())
    assert result.failed == result.pending_left == 0
    assert before == store.fetch_by_ids(excluded)


def test_ch04_status_reports_scoped_counts(setup_migration, monkeypatch):
    env = setup_migration
    migration.migrate_hybrid()
    ingest = _activate(monkeypatch, env)
    report = ingest.status_report()
    assert report['mysql_total'] == len(store.all_chunk_ids())
    assert report['eligible_total'] == report['target_count'] == 1
    assert report['eligible_pending'] == 0 and report['consistency'] == 'consistent'


def test_migration_cli_dry_run_verify_and_failure_exit(setup_migration, monkeypatch, capsys):
    from app.kb import ingest
    env = setup_migration
    assert ingest.main(['migrate-hybrid', '--dry-run']) == 0
    assert env.collections == {}
    assert ingest.main(['migrate-hybrid']) == 0
    assert ingest.main(['verify-hybrid']) == 0
    next(iter(env.collections['ch04_fake'].values()))['text'] = 'stale'
    assert ingest.main(['verify-hybrid']) == 1
    capsys.readouterr()


def test_preview_explicit_version_ignores_default(setup_migration, monkeypatch):
    from app.kb import ingest
    selected = []
    monkeypatch.setattr(ingest, 'get_sources', lambda **kw: selected.append(kw) or [setup_migration.source])
    assert ingest.main(['preview', '--corpus-version', 'ch04']) == 0
    assert selected == [{'corpus_version': 'ch04'}]


def test_schema_failure_precedes_mysql_writes(setup_migration, monkeypatch):
    before = store.all_chunk_ids()
    def incompatible(name):
        raise ValueError('incompatible schema')
    monkeypatch.setattr(migration.milvus, 'ensure_hybrid_collection', incompatible)
    with pytest.raises(ValueError, match='schema'):
        migration.migrate_hybrid()
    assert store.all_chunk_ids() == before


def test_iterator_over_5000_rows_is_exact_and_closed(monkeypatch):
    from app.kb import milvus
    records = [{'id': str(n)} for n in range(5101)]
    batches = iter([records[:5000], records[5000:], []])
    closed = []
    fake = SimpleNamespace(next=lambda: next(batches), close=lambda: closed.append(True))
    monkeypatch.setattr(milvus, 'get_client', lambda: SimpleNamespace(query_iterator=lambda **kw: fake))
    assert list(milvus.iter_records('test')) == records
    assert closed == [True]


def test_wrong_target_manifest_fails_closed(setup_migration, monkeypatch):
    env = setup_migration
    migration.migrate_hybrid()
    settings = env.settings.model_copy(update={'hybrid_collection': 'different'})
    monkeypatch.setattr(migration, 'get_settings', lambda: settings)
    with pytest.raises(ValueError, match='目标'):
        migration.load_manifest()


def test_pending_failure_can_resume_without_touching_excluded(setup_migration, monkeypatch):
    env = setup_migration
    migration.migrate_hybrid()
    _activate(monkeypatch, env)
    row_id = store.insert_chunks([store.ChunkCandidate('售后', '新问题', '新回答', '人工', '人工录入', False)])[0]
    def fail(name):
        raise RuntimeError('flush failed')
    env.client.flush = fail
    result = dualwrite.vectorize_pending()
    assert (result.processed, result.failed, result.pending_left) == (0, 1, 1)
    assert store.fetch_by_ids([row_id])[row_id].vectorize_status == 'pending'
    env.client.flush = lambda name: None
    assert dualwrite.vectorize_pending().processed == 1
    assert migration.verify_hybrid()['status'] == 'consistent'


def test_verify_over_5000_does_not_accept_same_count_wrong_ids(setup_migration, monkeypatch):
    env = setup_migration
    migration.migrate_hybrid()
    row = next(store.iter_chunks_by_ids(migration.eligible_chunk_ids()))
    from dataclasses import replace
    rows = [replace(row, id=n) for n in range(1, 5102)]
    monkeypatch.setattr(migration, 'eligible_chunk_ids', lambda: set(range(1, 5102)))
    monkeypatch.setattr(store, 'iter_chunks_by_ids', lambda ids: iter(rows))
    records = [migration.record_for(r) for r in rows]
    records[-1]['id'] = '99999'
    monkeypatch.setattr(migration.milvus, 'iter_records', lambda n: iter(records))
    report = migration.verify_hybrid()
    assert report['status'] == 'mismatch'
    assert report['missing_in_milvus'] == ['5101']
    assert report['orphan_in_milvus'] == ['99999']
