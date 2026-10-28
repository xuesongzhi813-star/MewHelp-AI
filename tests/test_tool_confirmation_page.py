from pathlib import Path


def test_chat_loads_ticket_preview_and_preserves_old_action_modules():
    root=Path('app/static')
    page=(root/'index.html').read_text(encoding='utf-8')
    assert '/static/chat-tool-confirmation.js' in page
    assert 'createToolConfirmationView' in page and 'tool_confirmation' in page
    assert '/static/chat-actions.js' in page and '/static/chat-refund.js' in page
    module=(root/'chat-tool-confirmation.js').read_text(encoding='utf-8')
    assert '/api/chat/actions/tool-confirmation' in module
    assert '确认提交' in module and '取消' in module
    assert 'textContent' in module and 'innerHTML' not in module
