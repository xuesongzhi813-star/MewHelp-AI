async def test_chat_page_loads_actions_module_and_confirmation_api(client):
    page = await client.get('/')
    assert page.status_code == 200
    script = await client.get('/static/chat-actions.js')
    assert script.status_code == 200
    assert 'javascript' in script.headers['content-type']
    # Confirmation must be rejected at the API boundary before touching runtime.
    response = await client.post('/api/chat/actions/ticket', json={'session_id':'s', 'action_id':'a', 'confirmed':False})
    assert response.status_code == 422
