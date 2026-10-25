"""Anonymous grouping identities, never an authentication mechanism."""
import re
from app.context.contracts import ConversationIdentity


def canonical_session_id(user_id: str, conversation_id: int) -> str:
    if not re.fullmatch(r'[0-9a-f]{32}', user_id) or conversation_id <= 0:
        raise ValueError('invalid conversation identity')
    return f'u:{user_id}:c:{conversation_id}'


def parse_canonical(session_id: str) -> ConversationIdentity | None:
    if not session_id.startswith('u:'):
        return None
    match = re.fullmatch(r'u:([0-9a-f]{32}):c:([1-9][0-9]*)', session_id)
    if not match:
        raise ValueError('invalid canonical session_id')
    return ConversationIdentity(match[1], int(match[2]), session_id)
