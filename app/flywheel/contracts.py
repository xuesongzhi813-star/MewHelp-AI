from dataclasses import dataclass, field


@dataclass(frozen=True)
class TurnIdentity:
    session_id: str
    conversation_id: int
    user_message_id: int
    request_id: str
    trace_id: str
    assistant_message_id: int | None = None
    checkpoint_id: str | None = None


@dataclass
class EvidenceSnapshot:
    identity: TurnIdentity | None
    snapshot_status: str
    hits: list[dict]
    processing: dict = field(default_factory=dict)
    schema_version: int = 1
    retrieval_strategy: str | None = None
    profile_version: str | None = None


@dataclass(frozen=True)
class PoolEvent:
    identity: TurnIdentity | None
    source: str
    raw_question: str
    reason: str
    snapshot: EvidenceSnapshot
    conversation_id: int | None = None


@dataclass(frozen=True)
class RecordResult:
    id: int
    created: bool
