"""ORM 模型:映射既有四表(SQLAlchemy 2.0 declarative)。

仅做映射,绝不 create_all / 改表——表结构由本机 MySQL melhelp 库既有 DDL 维护;
created_at/updated_at 全部交给数据库默认值(CURRENT_TIMESTAMP / ON UPDATE)。
"""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, Enum, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """declarative 基类。"""


class Conversation(Base):
    """会话表:一条 user_id 可有多轮会话。"""

    __tablename__ = "conversations"

    # BIGINT UNSIGNED AUTO_INCREMENT 主键(UNSIGNED 由数据库 DDL 维护,此处仅映射)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_upto_msg_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    layer1_from_msg_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(
        Enum("进行中", "已转人工", "已结束", name="conversation_status"),
        nullable=False,
        default="进行中",
        server_default="进行中",
    )
    # 时间列交给数据库默认值,插入时无需赋值
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.current_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.current_timestamp(),
        server_onupdate=func.current_timestamp(),  # ON UPDATE CURRENT_TIMESTAMP 由数据库执行
    )


class ConversationSummary(Base):
    """Append-only summary segments; schema is maintained outside this project."""
    __tablename__ = 'conversation_summaries'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('conversations.id'))
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    from_msg_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    upto_msg_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.current_timestamp())


class Faq(Base):
    """FAQ 表:常见问答知识库。"""

    __tablename__ = "faq"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    question: Mapped[str] = mapped_column(String(512), nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.current_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.current_timestamp(),
        server_onupdate=func.current_timestamp(),
    )


class Message(Base):
    """消息表:会话内的逐条消息,含工具调用记录。"""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("conversations.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(
        Enum("user", "assistant", "tool", name="message_role"), nullable=False
    )
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 工具调用参数/结果用 JSON 列存储
    tool_calls: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.current_timestamp()
    )


class Ticket(Base):
    """工单表:转人工后创建的售后/投诉/咨询工单。"""

    __tablename__ = "tickets"

    # 业务工单号作主键,非自增
    ticket_no: Mapped[str] = mapped_column(String(32), primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("conversations.id"), nullable=False
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    ticket_type: Mapped[str] = mapped_column(
        Enum("售后", "投诉", "咨询", name="ticket_type"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        Enum("待处理", "已处理", name="ticket_status"),
        nullable=False,
        default="待处理",
        server_default="待处理",
    )
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.current_timestamp()
    )


class KnowledgeChunk(Base):
    """知识库切片表:一条问答切片及其向量化状态(表由既有 DDL 维护)。"""

    __tablename__ = "knowledge_chunks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(String(255), nullable=False)
    questions: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    section_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_key_clause: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    prev_chunk_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    next_chunk_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    vector_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    vectorize_status: Mapped[str] = mapped_column(
        Enum("pending", "done", name="vectorize_status"),
        nullable=False,
        default="pending",
        server_default="pending",
    )
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.current_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.current_timestamp(),
        server_onupdate=func.current_timestamp(),
    )


class QaExtractionStaging(Base):
    """问答挖掘暂存表:每批抽取结果待人工保留/丢弃(表由既有 DDL 维护)。"""

    __tablename__ = "qa_extraction_staging"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    batch_no: Mapped[str] = mapped_column(String(64), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Enum("extracted", "kept", "discarded", name="staging_status"),
        nullable=False,
        default="extracted",
        server_default="extracted",
    )
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.current_timestamp()
    )


class LowConfidenceQuestion(Base):
    """映射已有问题池；本章不创建或修改表。"""

    __tablename__ = 'low_confidence_questions'

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey('conversations.id'), nullable=True
    )
    raw_question: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(
        Enum('retrieval_low_conf', 'self_check', 'user_feedback', name='low_confidence_source'),
        nullable=False,
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.current_timestamp())
