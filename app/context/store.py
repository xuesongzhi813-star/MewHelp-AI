"""Short transactions over existing tables. No schema creation or migration."""
from langchain_core.messages import AIMessage
from threading import RLock
from dataclasses import replace
from sqlalchemy import select, func

from app.db import get_session_local
from app.models import Conversation, ConversationSummary, Message
from app.context.contracts import ConversationIdentity, StoredMessage, SummaryRow, ContextSnapshot
from app.context.identity import canonical_session_id


def _summary(row):
    return SummaryRow(row.id, row.conversation_id, row.seq, row.from_msg_id, row.upto_msg_id, row.content)


class ConversationStore:
    def __init__(self, session_factory=None):
        self._factory = session_factory

    def session(self):
        return (self._factory or get_session_local())()

    def create_conversation(self, user_id):
        canonical_session_id(user_id, 1)  # validate before writing
        with self.session() as s, s.begin():
            row = Conversation(user_id=user_id)
            s.add(row)
            s.flush()
            return ConversationIdentity(user_id, row.id, canonical_session_id(user_id, row.id))

    def get_owned(self, conversation_id, user_id):
        with self.session() as s:
            row = s.get(Conversation, conversation_id)
            if row is None or row.user_id != user_id:
                raise LookupError('conversation not found')
            return ConversationIdentity(user_id, row.id, canonical_session_id(user_id, row.id))

    @staticmethod
    def _lock(s, cid):
        row = s.scalar(select(Conversation).where(Conversation.id == cid).with_for_update())
        if row is None:
            raise LookupError('conversation not found')
        return row

    def read_context(self, conversation_id):
        with self.session() as s:
            row = s.get(Conversation, conversation_id)
            if row is None:
                raise LookupError('conversation not found')
            summaries = s.scalars(select(ConversationSummary).where(
                ConversationSummary.conversation_id == conversation_id).order_by(ConversationSummary.seq)).all()
            return ContextSnapshot(conversation_id, row.summary_upto_msg_id, row.layer1_from_msg_id,
                                   [_summary(x) for x in summaries])

    def read_messages(self, conversation_id, *, from_id=None, upto_id=None):
        # Read the preceding users as well so partial ranges still have true turn IDs.
        with self.session() as s:
            rows = s.scalars(select(Message).where(Message.conversation_id == conversation_id)
                             .order_by(Message.id)).all()
            result, turn = [], 0
            for row in rows:
                if row.role == 'user':
                    turn = row.id
                if (from_id is None or row.id >= from_id) and (upto_id is None or row.id <= upto_id):
                    result.append(StoredMessage(row.id, conversation_id, row.role, row.content or '', turn,
                        row.created_at.isoformat(),row.tool_calls,row.tool_call_id))
            return result

    def append_user(self, conversation_id, text):
        with self.session() as s, s.begin():
            self._lock(s, conversation_id)
            row = Message(conversation_id=conversation_id, role='user', content=text)
            s.add(row)
            s.flush()
            return StoredMessage(row.id, conversation_id, 'user', text, row.id)

    def archive_final(self, conversation_id, turn_from_msg_id, message):
        if not isinstance(message, AIMessage) or message.tool_calls or not message.content:
            return None
        return self.append_final(conversation_id, turn_from_msg_id, str(message.content))

    def append_final(self, conversation_id, turn_from_msg_id, text):
        with self.session() as s, s.begin():
            self._lock(s, conversation_id)
            user = s.get(Message, turn_from_msg_id)
            if user is None or user.role != 'user' or user.conversation_id != conversation_id:
                raise ValueError('invalid final turn')
            next_user = s.scalar(select(func.min(Message.id)).where(Message.conversation_id == conversation_id,
                Message.role == 'user', Message.id > turn_from_msg_id))
            stmt = select(Message).where(Message.conversation_id == conversation_id,
                Message.role == 'assistant', Message.id > turn_from_msg_id, Message.tool_calls.is_(None))
            if next_user is not None:
                stmt = stmt.where(Message.id < next_user)
            slots = [r for r in s.scalars(stmt).all() if r.content]
            if slots:
                if len(slots) != 1 or slots[0].content != text:
                    raise ValueError('conflicting final slot')
                row = slots[0]
            elif next_user is not None:
                raise ValueError('cannot append final after next user')
            else:
                row = Message(conversation_id=conversation_id, role='assistant', content=text)
                s.add(row)
                s.flush()
            return StoredMessage(row.id, conversation_id, 'assistant', text, turn_from_msg_id)

    def advance_layer1(self, conversation_id, from_msg_id):
        with self.session() as s, s.begin():
            row = self._lock(s, conversation_id)
            target = s.get(Message, from_msg_id)
            if target is None or target.conversation_id != conversation_id or target.role != 'user':
                raise ValueError('layer1 must start at a user')
            if row.layer1_from_msg_id is None or from_msg_id > row.layer1_from_msg_id:
                row.layer1_from_msg_id = from_msg_id

    def append_summary(self, conversation_id, *, expected_upto, from_msg_id, upto_msg_id, content):
        if not content or len(content) > 200:
            raise ValueError('summary must contain 1..200 characters')
        with self.session() as s, s.begin():
            parent = self._lock(s, conversation_id)
            if parent.summary_upto_msg_id != expected_upto:
                return None
            rows = s.scalars(select(Message).where(Message.conversation_id == conversation_id,
                Message.id > (expected_upto or 0), Message.id <= upto_msg_id).order_by(Message.id)).all()
            if (not rows or rows[0].id != from_msg_id or rows[0].role != 'user'
                    or rows[-1].id != upto_msg_id or parent.layer1_from_msg_id is None
                    or upto_msg_id >= parent.layer1_from_msg_id):
                raise ValueError('invalid summary coverage')
            next_row = s.scalar(select(Message).where(Message.conversation_id == conversation_id,
                Message.id > upto_msg_id).order_by(Message.id).limit(1))
            if next_row is not None and next_row.role != 'user':
                raise ValueError('summary must cover complete turns')
            seq = (s.scalar(select(func.max(ConversationSummary.seq)).where(
                ConversationSummary.conversation_id == conversation_id)) or 0) + 1
            row = ConversationSummary(conversation_id=conversation_id, seq=seq,
                from_msg_id=from_msg_id, upto_msg_id=upto_msg_id, content=content)
            s.add(row)
            s.flush()
            parent.summary_upto_msg_id = upto_msg_id
            s.flush()
            return _summary(row)

    def list_conversations(self, user_id):
        with self.session() as s:
            rows = s.scalars(select(Conversation).where(Conversation.user_id == user_id)
                .order_by(Conversation.created_at.desc(), Conversation.id.desc())).all()
            result = []
            for row in rows:
                preview = s.scalar(select(Message.content).where(Message.conversation_id == row.id,
                    Message.role == 'user').order_by(Message.id).limit(1)) or ''
                has_summary = s.scalar(select(ConversationSummary.id).where(
                    ConversationSummary.conversation_id == row.id).limit(1)) is not None
                result.append(dict(id=row.id, session_id=canonical_session_id(user_id, row.id),
                    preview=preview[:80],status=row.status,created_at=row.created_at.isoformat(),
                    updated_at=row.updated_at.isoformat(),has_summary=has_summary))
            return result


class InMemoryConversationStore:
    """Rollback-free test double; IDs and immutable snapshots mirror the DB contract."""
    archive_final = ConversationStore.archive_final

    def __init__(self):
        self.identities, self.messages, self.contexts = {}, {}, {}
        self._next = 1
        self._lock = RLock()

    def create_conversation(self, user_id):
        with self._lock:
            cid = len(self.identities) + 1
            ident = ConversationIdentity(user_id, cid, canonical_session_id(user_id, cid))
            self.identities[cid] = ident
            self.messages[cid] = []
            self.contexts[cid] = ContextSnapshot(cid, None, None, [])
            return ident

    def get_owned(self, conversation_id, user_id):
        ident = self.identities.get(conversation_id)
        if not ident or ident.user_id != user_id:
            raise LookupError('conversation not found')
        return ident

    def read_context(self, conversation_id):
        row = self.contexts[conversation_id]
        return replace(row, summaries=list(row.summaries))

    def read_messages(self, conversation_id, *, from_id=None, upto_id=None):
        return [m for m in self.messages[conversation_id]
                if (from_id is None or m.id >= from_id) and (upto_id is None or m.id <= upto_id)]

    def append_user(self, conversation_id, text):
        with self._lock:
            row = StoredMessage(self._next, conversation_id, 'user', text, self._next)
            self._next += 1
            self.messages[conversation_id].append(row)
            return row

    def append_final(self, conversation_id, turn_from_msg_id, text):
        with self._lock:
            rows = self.messages[conversation_id]
            if not any(m.id == turn_from_msg_id and m.role == 'user' for m in rows):
                raise ValueError('invalid final turn')
            slots = [m for m in rows if m.turn_from_msg_id == turn_from_msg_id and m.role == 'assistant']
            if slots:
                if len(slots) != 1 or slots[0].content != text:
                    raise ValueError('conflicting final slot')
                return slots[0]
            if any(m.id > turn_from_msg_id and m.role == 'user' for m in rows):
                raise ValueError('cannot append final after next user')
            row = StoredMessage(self._next, conversation_id, 'assistant', text, turn_from_msg_id)
            self._next += 1
            rows.append(row)
            return row

    def advance_layer1(self, conversation_id, from_msg_id):
        with self._lock:
            if not any(m.id == from_msg_id and m.role == 'user' for m in self.messages[conversation_id]):
                raise ValueError('layer1 must start at a user')
            old = self.contexts[conversation_id]
            self.contexts[conversation_id] = replace(old, layer1_from_msg_id=max(old.layer1_from_msg_id or 0, from_msg_id))

    def append_summary(self, conversation_id, *, expected_upto, from_msg_id, upto_msg_id, content):
        with self._lock:
            old = self.contexts[conversation_id]
            if old.summary_upto_msg_id != expected_upto:
                return None
            rows = self.read_messages(conversation_id, from_id=(expected_upto or 0)+1, upto_id=upto_msg_id)
            after = self.read_messages(conversation_id, from_id=upto_msg_id+1)
            if (not content or len(content) > 200 or not rows or rows[0].id != from_msg_id
                    or rows[0].role != 'user' or rows[-1].id != upto_msg_id
                    or not old.layer1_from_msg_id or upto_msg_id >= old.layer1_from_msg_id
                    or (after and after[0].role != 'user')):
                raise ValueError('invalid summary coverage')
            row = SummaryRow(sum(len(s.summaries) for s in self.contexts.values())+1,
                conversation_id, len(old.summaries)+1, from_msg_id, upto_msg_id, content)
            self.contexts[conversation_id] = replace(old, summary_upto_msg_id=upto_msg_id,
                                                  summaries=[*old.summaries, row])
            return row

    def list_conversations(self, user_id):
        return [dict(id=i.conversation_id, session_id=i.session_id,
                     preview=next((m.content[:80] for m in self.messages[i.conversation_id] if m.role=='user'), ''),
                     status='进行中',created_at='',updated_at='',has_summary=bool(self.contexts[i.conversation_id].summaries))
                for i in reversed(list(self.identities.values())) if i.user_id == user_id]
