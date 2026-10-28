"""Catch stale permissions, incorrect write inference, and unsafe local configuration."""
import json
import pytest
from app.tooling.policy import PolicyLoader


def test_malformed_policy_revokes_mcp_and_writes(tmp_path):
    path=tmp_path/"policy.json"
    path.write_text(json.dumps({"servers":{"vendor":{"url":"http://127.0.0.1:8021/mcp","enabled":True}},
        "rules":{"vendor/lookup":{"enabled":True,"effect":"read"}}}),encoding="utf-8")
    loader=PolicyLoader(path)
    first=loader.load()
    assert first.rule("vendor","lookup").enabled
    path.write_text("{bad json",encoding="utf-8")
    bad=loader.load()
    assert not bad.valid
    assert not bad.rule("vendor","lookup").enabled
    assert bad.rule("builtin","query_order").enabled
    assert not bad.rule("builtin","create_ticket").enabled
    assert bad.version != first.version


def test_permission_reload_does_not_wait_for_mtime(tmp_path):
    path=tmp_path/"policy.json"
    path.write_text('{"servers":{},"rules":{}}',encoding="utf-8")
    loader=PolicyLoader(path)
    first=loader.load()
    path.write_text('{"servers":{},"rules":{"builtin/query_order":{"enabled":false,"effect":"read"}}}',encoding="utf-8")
    assert not loader.load().rule("builtin","query_order").enabled
    assert loader.load().version != first.version


@pytest.mark.parametrize("body",[
    '{"servers":{},"rules":{"x/t":{"enabled":"false","effect":"read"}}}',
    '{"servers":{},"rules":{"x/t":{"enabled":true,"effect":"admin"}}}',
    '{"servers":{},"rules":{},"rules":{}}',
    '{"servers":{"x":{"url":"https://example.org/mcp","enabled":true}},"rules":{}}',
    '{"servers":{},"rules":{"x/t":{"enabled":true,"effect":"read","routing_intents":["投诉"]}}}',
    '{"servers":{},"rules":{"builtin/create_ticket":{"enabled":true,"effect":"read"}}}',
])
def test_invalid_policy_fails_closed(tmp_path,body):
    path=tmp_path/"policy.json"
    path.write_text(body,encoding="utf-8")
    assert not PolicyLoader(path).load().valid


def test_server_write_tool_is_never_enabled(tmp_path):
    path=tmp_path/"policy.json"
    path.write_text('{"servers":{"x":{"url":"http://127.0.0.1:8021/mcp","enabled":true}},"rules":{"x/t":{"enabled":true,"effect":"write"}}}',encoding="utf-8")
    snapshot=PolicyLoader(path).load()
    assert snapshot.valid
    assert snapshot.rule("x","t").effect == "write"
    assert not snapshot.rule("x","t").enabled

