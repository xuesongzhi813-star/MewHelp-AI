import math
from typing import Protocol

from langchain_core.messages import BaseMessage


def estimate_tokens(text: str) -> int:
    """近似估算 token 数:CJK 字符 1 字 1 token,其余 4 字符 1 token。"""
    cjk = sum(1 for ch in text if _is_cjk(ch))
    other = len(text) - cjk
    return cjk + math.ceil(other / 4)


def _is_cjk(ch: str) -> bool:
    # CJK 统一表意文字基本区 U+4E00–U+9FFF
    return "一" <= ch <= "鿿"


def _message_tokens(message: BaseMessage) -> int:
    content = message.content
    return estimate_tokens(content if isinstance(content, str) else "")


def trim_history(messages: list[BaseMessage], budget: int) -> list[BaseMessage]:
    """从最新往旧累加消息,超出预算即止,返回按时间正序保留的消息。"""
    kept: list[BaseMessage] = []
    used = 0
    for message in reversed(messages):
        cost = _message_tokens(message)
        if used + cost > budget:
            break
        kept.append(message)
        used += cost
    kept.reverse()
    return kept


class SessionStore(Protocol):
    def get_history(self, session_id: str) -> list[BaseMessage]: ...

    def append_message(self, session_id: str, message: BaseMessage) -> None: ...


class InMemorySessionStore:
    """进程内会话存储,会话数超过 max_sessions 时按 FIFO 淘汰最旧会话。"""

    def __init__(self, max_sessions: int) -> None:
        self._max_sessions = max_sessions
        self._sessions: dict[str, list[BaseMessage]] = {}

    def get_history(self, session_id: str) -> list[BaseMessage]:
        return list(self._sessions.get(session_id, []))

    def append_message(self, session_id: str, message: BaseMessage) -> None:
        if session_id not in self._sessions:
            if self._sessions and len(self._sessions) >= self._max_sessions:
                oldest = next(iter(self._sessions))
                del self._sessions[oldest]
            self._sessions[session_id] = []
        self._sessions[session_id].append(message)
