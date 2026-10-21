from types import SimpleNamespace
import app.kb.sources as sources


async def test_source_whitelist_tracks_selected_corpus(client, monkeypatch):
    monkeypatch.setattr(sources, 'get_settings', lambda: SimpleNamespace(kb_corpus_version='ch04'))
    accepted = await client.get('/api/kb/source', params={'path': 'data/kb/ch04/product-specs.md'})
    rejected = await client.get('/api/kb/source', params={'path': 'data/kb/product-specs.md'})
    assert accepted.status_code == 200
    assert 'MH-DJ101' in accepted.json()['markdown']
    assert rejected.status_code == 404
