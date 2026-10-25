import json
import pytest
from scripts.demo_ch07 import profile_values, pipeline_identity, load_ch07_profile
from app.config import Settings
from app.context.budget import validate_startup
from app.context.assembly import fixed_agent_schemas


def test_public_demo_breakdown_uses_actual_prefix():
    settings=Settings(_env_file=None,**profile_values('demo'))
    result=validate_startup(settings,schemas=fixed_agent_schemas())
    assert (result.history,result.layer1,result.layer2)==(5650,3954,1695)
    assert result.system==2000


def test_profile_rejects_changed_pipeline_and_never_rebinds_old(tmp_path):
    target=tmp_path/'profile.json'
    target.write_text(json.dumps(dict(identity={'test':1},split='calibration',threshold=.5,ch07_pipeline={'code_hash':'old'})))
    before=target.read_bytes()
    with pytest.raises(ValueError):
        load_ch07_profile(target,identity={'test':1})
    assert target.read_bytes()==before
