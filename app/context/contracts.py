"""Serializable context data, without model clients or database sessions."""
from dataclasses import asdict, dataclass
from langchain_core.messages import BaseMessage


@dataclass(frozen=True)
class ConversationIdentity:
    user_id: str
    conversation_id: int
    session_id: str
    legacy: bool = False


@dataclass(frozen=True)
class StoredMessage:
    id: int
    conversation_id: int
    role: str
    content: str
    turn_from_msg_id: int
    created_at: str = ''
    tool_calls: list | None = None
    tool_call_id: str | None = None


@dataclass(frozen=True)
class SummaryRow:
    id: int
    conversation_id: int
    seq: int
    from_msg_id: int
    upto_msg_id: int
    content: str


@dataclass(frozen=True)
class ContextSnapshot:
    conversation_id: int
    summary_upto_msg_id: int | None
    layer1_from_msg_id: int | None
    summaries: list[SummaryRow]


@dataclass(frozen=True)
class MessageRef:
    db_id: int | None
    turn_from_msg_id: int


@dataclass(frozen=True)
class HistoryView:
    conversation_id: int
    summaries: tuple[SummaryRow, ...]
    messages: list[BaseMessage]
    text: str
    tokens: int


@dataclass(frozen=True)
class LayeredView:
    layer1: list[BaseMessage]
    layer2: list[BaseMessage]
    current: list[BaseMessage]
    layer1_from_msg_id: int
    layer2_tokens: int
    history: HistoryView


@dataclass(frozen=True)
class SummaryBatch:
    conversation_id: int
    expected_upto: int | None
    from_msg_id: int
    upto_msg_id: int
    captured_layer1_from: int
    messages: tuple[BaseMessage, ...]
    old_summaries: tuple[SummaryRow, ...]
    config_identity: str = ''
    refs: dict | None = None
    request_id: str = ''
    layer2_tokens: int = 0
    layer2_budget: int = 0


@dataclass(frozen=True)
class ContextBudget:
    window: int
    system: int
    evidence: int
    summary: int
    output: int
    safety: int
    peak: int
    history: int
    layer1: int
    layer2: int

    def to_dict(self) -> dict:
        return asdict(self)
