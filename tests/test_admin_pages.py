"""后台页面冒烟测试:/admin、/kb 与共享脚本可访问且带关键标记。"""


async def test_admin_pages_served(client):
    for path in ("/admin", "/kb", "/static/admin.js"):
        resp = await client.get(path)
        assert resp.status_code == 200
    assert "知识库" in (await client.get("/admin")).text
    assert "手工录入" in (await client.get("/kb")).text


async def test_admin_pages_share_nav_and_script(client):
    admin = (await client.get("/admin")).text
    kb = (await client.get("/kb")).text
    script = (await client.get("/static/admin.js")).text

    assert 'data-page="admin"' in admin
    assert 'data-page="kb"' in kb
    assert "/static/admin.js" in admin
    assert "/static/admin.js" in kb
    assert "window.Admin" in script
    assert "pollJob" in script
    assert "/api/jobs/" in script


async def test_chat_page_is_unchanged_and_served(client):
    resp = await client.get("/")
    assert resp.status_code == 200
    assert "智能客服" in resp.text
    assert "手工录入" not in resp.text
