"""pytest 全局配置。

在 import 任何 app 模块之前,用 os.environ.setdefault 注入必填配置兜底,
让 fresh clone(无 .env)也能跑通全部测试;本机已有同名环境变量时不覆盖。
"""

import os

os.environ.setdefault("LLM_BASE_URL", "https://api.deepseek.com")
os.environ.setdefault("LLM_API_KEY", "sk-test")
os.environ.setdefault("LLM_MODEL", "deepseek-flash")
os.environ.setdefault("LLM_EXTRA_BODY", '{"thinking": {"type": "disabled"}}')
os.environ.setdefault("SHOP_NAME", "MewHelp 商城")
os.environ.setdefault("AGENT_NAME", "小喵")

import httpx  # noqa: E402
import pytest  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
