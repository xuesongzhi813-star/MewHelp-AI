from app.tooling.mcp_client import MCPManager
from app.tooling.policy import PolicyLoader
from tests.test_mcp_servers import running_server
import json


async def test_local_mcp_ignores_unrelated_system_proxy(tmp_path,monkeypatch):
    async with running_server('app.mcp_servers.logistics',tmp_path) as url:
        for key in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'):
            monkeypatch.setenv(key,'http://127.0.0.1:1')
        monkeypatch.setenv('NO_PROXY','');monkeypatch.setenv('no_proxy','')
        path=tmp_path/'policy.json'
        path.write_text(json.dumps({'servers':{'logistics':{'url':url,'enabled':True}},'rules':{}}))
        manager=MCPManager()
        try:
            tools=await manager.discover(PolicyLoader(path).load())
            assert [d.name for d in tools['logistics']]==['query_logistics']
        finally:
            await manager.aclose()
