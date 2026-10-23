"""Demo startup must reject a stale ch06 pipeline even with a valid KB profile."""
import json

import pytest

from scripts import demo_ch06


def test_pipeline_identity_changes_with_policy_implementation(tmp_path):
    source = tmp_path / 'app/kb'
    source.mkdir(parents=True)
    (source / 'multi_query.py').write_text('version = 1', encoding='utf-8')
    first = demo_ch06.pipeline_identity(root=tmp_path)
    (source / 'multi_query.py').write_text('version = 2', encoding='utf-8')
    assert demo_ch06.pipeline_identity(root=tmp_path) != first


@pytest.mark.parametrize('binding', [None, {'code_hash': 'old'}])
def test_demo_rejects_missing_or_stale_pipeline_binding(tmp_path, monkeypatch, binding):
    from scripts import eval_retrieval
    profile = {'threshold': .25, 'ch06_pipeline': binding}
    monkeypatch.setattr(eval_retrieval, 'load_profile', lambda path, identity: profile)
    monkeypatch.setattr(demo_ch06, 'pipeline_identity', lambda: {'code_hash': 'current'})
    with pytest.raises(ValueError, match='ch06'):
        demo_ch06.load_ch06_profile(tmp_path / 'profile.json', identity={})


def test_new_binding_preserves_original_calibration_identity(tmp_path, monkeypatch):
    from scripts import eval_retrieval
    profile = {'identity': {'code_hash': 'base'}, 'split': 'calibration', 'threshold': .25}
    path = tmp_path / 'profile.json'
    path.write_text(json.dumps(profile), encoding='utf-8')
    monkeypatch.setattr(eval_retrieval, '_identity', lambda: profile['identity'])
    monkeypatch.setattr(eval_retrieval, 'load_profile', lambda path, identity: json.loads(path.read_text()))
    monkeypatch.setattr(demo_ch06, 'pipeline_identity', lambda: {'code_hash': 'current'})
    demo_ch06.bind_calibrated_profile(path)
    bound = demo_ch06.load_ch06_profile(path, identity=profile['identity'])
    assert bound['identity'] == profile['identity']
    assert bound['ch06_pipeline'] == {'code_hash': 'current'}
    assert bound['calibration_scope'] == 'existing_single_query_calibration'
