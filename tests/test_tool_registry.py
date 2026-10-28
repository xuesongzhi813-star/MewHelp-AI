"""Catch mutable/ambiguous tool catalogs and unauthorized dynamic publication."""
import pytest
from app.tooling.contracts import ToolDefinition
from app.tooling.registry import ToolRegistry
from app.tooling.policy import PolicyLoader
import json


def tool(name="lookup", source="vendor"):
    return ToolDefinition(name=name, description="查询演示记录",
        input_schema={"type":"object","properties":{"id":{"type":"string"}},"required":["id"],"additionalProperties":False},
        source=source, remote_name=name)


def policy(tmp_path, rules=None):
    path=tmp_path/"policy.json"
    path.write_text(json.dumps({"servers":{"vendor":{"url":"http://127.0.0.1:8021/mcp","enabled":True}},
        "rules":rules or {"vendor/lookup":{"enabled":True,"effect":"read"}}}),encoding="utf-8")
    return PolicyLoader(path).load()


def test_registry_rejects_collision_and_preserves_snapshot(tmp_path):
    registry=ToolRegistry()
    definition=tool()
    registry.register(definition)
    old=registry.snapshot(policy(tmp_path))
    with pytest.raises(ValueError):
        registry.register(tool())
    registry.unregister("vendor","lookup")
    assert old.schemas[0]["function"]["name"] == "mcp__vendor__lookup"
    assert registry.snapshot(policy(tmp_path)).schemas == ()


def test_registered_schema_cannot_be_mutated_through_original(tmp_path):
    registry=ToolRegistry()
    definition=tool()
    registry.register(definition)
    definition.input_schema["required"].append("secret")
    assert registry.snapshot(policy(tmp_path)).schemas[0]["function"]["parameters"]["required"] == ["id"]


def test_unknown_mcp_denied_despite_read_only_hint(tmp_path):
    registry=ToolRegistry()
    definition=tool("unknown")
    definition.input_schema["readOnlyHint"]=True
    registry.register(definition)
    assert registry.snapshot(policy(tmp_path)).schemas == ()


def test_replace_source_is_atomic_on_invalid_definition(tmp_path):
    registry=ToolRegistry()
    registry.register(tool())
    with pytest.raises(ValueError):
        registry.replace_source("vendor",[tool("new"),tool("bad name")])
    assert registry.snapshot(policy(tmp_path)).schemas[0]["function"]["name"] == "mcp__vendor__lookup"


def test_alias_collision_never_overrides_builtin(tmp_path):
    registry=ToolRegistry()
    registry.register(tool("query_order","builtin"))
    registry.register(tool())
    rules={"vendor/lookup":{"enabled":True,"effect":"read","alias":"query_order"}}
    with pytest.raises(ValueError):
        registry.snapshot(policy(tmp_path,rules))


def test_definition_update_changes_next_snapshot_only(tmp_path):
    registry=ToolRegistry()
    registry.register(tool())
    before=registry.snapshot(policy(tmp_path))
    changed=tool()
    changed.input_schema["properties"]["id"]["maxLength"]=10
    registry.register(changed,replace=True)
    after=registry.snapshot(policy(tmp_path))
    assert before.version != after.version
    assert "maxLength" not in before.schemas[0]["function"]["parameters"]["properties"]["id"]

