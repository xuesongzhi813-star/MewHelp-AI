import pytest

from app.context.identity import canonical_session_id, parse_canonical


def test_canonical_roundtrip_and_legacy():
    user = 'a' * 32
    identity = parse_canonical(canonical_session_id(user, 123))
    assert (identity.user_id, identity.conversation_id, identity.legacy) == (user, 123, False)
    assert parse_canonical('old-session') is None


@pytest.mark.parametrize('value', ['u:bad:c:1', 'u:' + 'a'*32 + ':c:0', 'u:' + 'a'*32 + ':c:01'])
def test_malformed_canonical_is_rejected(value):
    with pytest.raises(ValueError):
        parse_canonical(value)


def test_memory_store_has_same_append_only_contract():
    from app.context.store import InMemoryConversationStore
    db = InMemoryConversationStore()
    a = db.create_conversation('b'*32)
    user = db.append_user(a.conversation_id, 'ORD-1001没收到')
    final = db.append_final(a.conversation_id, user.id, '待查询')
    assert db.append_final(a.conversation_id, user.id, '待查询') == final
    next_user = db.append_user(a.conversation_id, '继续')
    db.advance_layer1(a.conversation_id, next_user.id)
    summary = db.append_summary(a.conversation_id, expected_upto=None,
        from_msg_id=user.id, upto_msg_id=final.id, content='ORD-1001没收到，待查询。')
    assert summary.seq == 1
    assert db.read_context(a.conversation_id).summary_upto_msg_id == final.id
    assert db.get_owned(a.conversation_id, a.user_id) == a
    assert db.list_conversations(a.user_id)[0]['has_summary']
