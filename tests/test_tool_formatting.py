"""Project trusted fields and translate enums without fabricating business success."""
import json
from app.tooling.contracts import RawToolResult,ToolDefinition
from app.tooling.formatting import format_result
from app.tooling.policy import ToolRule


def definition(name="lookup"):
    return ToolDefinition(name,"查询",{"type":"object"},"builtin")


def test_only_allowed_fields_and_human_enum_values_are_returned():
    raw=RawToolResult({"status":"transit","secret":"password","order_id":"1001"})
    rule=ToolRule(enabled=True,output_fields=("status","order_id"),enum_map={"status":{"transit":"运输中"}})
    result=format_result(raw,definition(),rule)
    assert json.loads(result.content)=={"status":"运输中","order_id":"1001"}
    assert "\\u" not in result.content
    assert "password" not in str(result.artifact)


def test_unknown_enum_is_explicitly_unknown():
    result=format_result(RawToolResult({"status":"new_code"}),definition(),
        ToolRule(enabled=True,output_fields=("status",),enum_map={"status":{"known":"已完成"}}))
    assert json.loads(result.content)["status"]=="未知状态"


def test_faq_text_and_evidence_artifact_are_preserved():
    artifact=object()
    result=format_result(RawToolResult("1. 退货政策\n→ 七日内",artifact),definition("query_faq"),ToolRule(enabled=True))
    assert result.content=="1. 退货政策\n→ 七日内" and result.artifact is artifact


def test_nested_unprojected_fields_do_not_leak():
    rule=ToolRule(enabled=True,output_fields=("events.status","events.time"))
    result=format_result(RawToolResult({"events":[{"status":"运输中","time":"今天","internal_ip":"secret"}]}),definition(),rule)
    assert json.loads(result.content)=={"events":[{"status":"运输中","time":"今天"}]}


def test_real_logistics_policy_projects_and_translates_nested_events():
    from pathlib import Path
    from app.tooling.policy import PolicyLoader
    rule=PolicyLoader(Path(__file__).resolve().parents[1]/"config/tool_policy.json").load().rule("logistics","query_logistics")
    raw=RawToolResult({"current_status":"transit","events":[{"time":"今天","status":"transit","location":"演示中心","internal_ip":"secret"}]})
    result=format_result(raw,definition(),rule)
    assert "secret" not in result.content
    assert json.loads(result.content)["events"][0]["status"]=="运输中"

