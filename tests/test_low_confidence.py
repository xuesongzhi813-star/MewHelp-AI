"""真实既有表的写入/事务边界；测试只回滚自身创建的样本。"""
import uuid

import pytest
from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session, sessionmaker

from app.db import get_engine
from app.kb.low_confidence import record_low_confidence
from app.models import Conversation, LowConfidenceQuestion


@pytest.fixture
def lcq_tx(monkeypatch):
    import app.kb.low_confidence as repository
    with get_engine().connect() as conn:
        outer = conn.begin()
        factory = sessionmaker(bind=conn, join_transaction_mode='create_savepoint')
        monkeypatch.setattr(repository, 'get_session_local', lambda: factory)
        try:
            yield factory
        finally:
            outer.rollback()


def test_low_confidence_uses_existing_table_and_outer_transaction(lcq_tx):
    raw = f'库外问题-{uuid.uuid4().hex}'
    commits = []
    with lcq_tx() as session:
        event.listen(session, 'before_commit', lambda s: commits.append(True))
        conversation = Conversation(user_id=f'ch04-test-{uuid.uuid4().hex}')
        session.add(conversation)
        session.flush()
        row_id = record_low_confidence(raw, conversation.id, 'self_check',
                                      '证据缺少条件', session=session)
        row = session.get(LowConfidenceQuestion, row_id)
        assert row.raw_question == raw and row.conversation_id == conversation.id
        assert row.source == 'self_check' and row.reason == '证据缺少条件'
        assert row.created_at is not None
        assert commits == []
        session.rollback()
        assert session.scalar(select(LowConfidenceQuestion).where(
            LowConfidenceQuestion.raw_question == raw)) is None


def test_owned_session_persists_once_inside_outer_rollback(lcq_tx):
    raw = f'未知型号-{uuid.uuid4().hex}'
    row_id = record_low_confidence(raw, None, 'retrieval_low_conf', '无匹配')
    with lcq_tx() as session:
        assert session.get(LowConfidenceQuestion, row_id).raw_question == raw


def test_invalid_source_is_rejected_before_database_write(lcq_tx):
    with pytest.raises(ValueError):
        record_low_confidence('问', None, 'user_feedback', '本章不写反馈')


def test_mapping_matches_existing_low_confidence_columns():
    real = {column['name'] for column in inspect(get_engine()).get_columns('low_confidence_questions')}
    assert set(LowConfidenceQuestion.__table__.columns.keys()) == real
