from pathlib import Path
from scripts.demo_ch08 import profile_values,retrieval_identity


def test_ch08_uses_independent_output_capacity_and_profile_identity(tmp_path):
    assert profile_values('default')['model_context_window']==128000
    assert profile_values('default')['agent_decision_max_tokens']==2600
    assert profile_values('demo')['max_agent_steps']==3
    assert profile_values('demo')['model_context_window']==24000
    assert profile_values('demo')['agent_decision_max_tokens']==2600
    (tmp_path/'app/kb').mkdir(parents=True)
    (tmp_path/'app/kb/query.py').write_text('a=1')
    first=retrieval_identity(root=tmp_path)
    (tmp_path/'config').mkdir();(tmp_path/'config/tool_policy.json').write_text('{}')
    assert retrieval_identity(root=tmp_path)==first
    (tmp_path/'app/kb/query.py').write_text('a=2')
    assert retrieval_identity(root=tmp_path)!=first
