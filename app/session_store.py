"""MySQL 会话存储:多轮历史落库与重建(含工具调用消息)。

实现 app.history.SessionStore Protocol,与 InMemorySessionStore 可互换:
- append_message 把 LangChain 消息按 role 映射进 messages 表
  (user → 'user' / AIMessage → 'assistant' / ToolMessage → 'tool');
- get_history 按 id 升序读回 messages,重建等价的 LangChain 消息列表,
  AIMessage 的 tool_calls 从 JSON 列还原(只存 name/args/id 三键,
  AIMessage 构造时会自动补上 "type": "tool_call")。

设计取舍:
- SystemMessage 等其他类型不落库:system prompt 每轮由 build_system_prompt
  现拼,不是会话历史的一部分;
- AIMessage 空 content(纯工具调用)存 NULL 而非空串:列本身可空,
  NULL 语义即「无文本内容」,重建时统一回填成空串;
- 每次操作都走 get_or_create_conversation 重新定位会话(不进程内缓存
  conversation_id):实现简单、无缓存失效问题,多查一次的损耗演示场景可接受;
- 会话获取与 app.tools 同款手法:模块级引用 get_session_local(可被
  monkeypatch 注入回滚会话),每次操作短 session,不自持长连接。
"""

import logging

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)
from sqlalchemy import select

from app.db import get_session_local
from app.history import SessionStore
from app.models import Conversation, Message

logger = logging.getLogger(__name__)


class MySQLSessionStore(SessionStore):
    """落库版会话存储:一个 session_id 对应 conversations.user_id。

    显式继承 SessionStore Protocol:满足静态类型检查(赋值给
    SessionStore 注解即静态断言)。注意运行时不能用 isinstance 验证——
    SessionStore 未标 @runtime_checkable,isinstance 会抛 TypeError,
    运行时校验见测试的结构化断言(方法存在且可调用)。
    """

    def get_or_create_conversation(self, session_id: str) -> int:
        """取该 user_id 最新一条「进行中」会话的 id,没有则新建一条。

        返回 conversation.id,供 get_history / append_message 定位消息。
        注意:并发下同一 user_id 可能各自新建一条「进行中」会话(查无即插,
        无唯一约束兜底)——演示场景可接受,线上需加唯一索引或锁。
        """
        with get_session_local()() as session:
            with session.begin():
                conversation = session.scalar(
                    select(Conversation)
                    .where(
                        Conversation.user_id == session_id,
                        Conversation.status == "进行中",
                    )
                    .order_by(Conversation.id.desc())
                    .limit(1)
                )
                if conversation is not None:
                    return conversation.id
                conversation = Conversation(user_id=session_id)
                session.add(conversation)
                session.flush()  # flush 拿回自增主键
                return conversation.id

    def get_history(self, session_id: str) -> list[BaseMessage]:
        """重建该会话的全部历史消息(按消息 id 升序,即写入顺序)。

        未知 session(无「进行中」会话)会先建会话再查,天然返回空列表。
        重建时做毒历史防御(I-1):尾部未配对的 AIMessage(tool_calls) 直接丢弃,
        详见 _rebuild_messages。
        """
        conversation_id = self.get_or_create_conversation(session_id)
        with get_session_local()() as session:
            with session.begin():
                rows = session.scalars(
                    select(Message)
                    .where(Message.conversation_id == conversation_id)
                    .order_by(Message.id.asc())
                ).all()
                # 在 session 上下文内完成重建,避免 commit 后属性过期
                return self._rebuild_messages(
                    [self._row_to_message(row) for row in rows],
                    session_id=session_id,
                    conversation_id=conversation_id,
                )

    @staticmethod
    def _rebuild_messages(
        messages: list[BaseMessage], *, session_id: str, conversation_id: int
    ) -> list[BaseMessage]:
        """毒历史防御(I-1):丢弃尾部未配对的 AIMessage(tool_calls)。

        正常路径不可达——append_message 逐条落库,AIMessage(tool_calls) 与
        其 ToolMessage 本应成对写入;唯一现实缺口是 AIMessage(tool_calls)
        落库后、ToolMessage 落库前进程崩溃(或 ToolMessage 落库失败),
        该 AIMessage 会孤悬在序列尾部。把这样的历史原样发给 OpenAI API
        会因「tool_calls 无对应 tool 结果」被拒,整个会话每轮都 400。
        防御口径:尾部未配对的 AIMessage(tool_calls) 从重建结果中丢弃,
        把「会话每轮 400」降级为「丢一轮工具记录但会话可用」。

        只处理尾部未配对:序列中间出现未配对属于更严重的库损坏(比如
        ToolMessage 被手工删除),保持原样重建、不做额外处理,交由
        上层报错暴露问题。配对判定:紧随其后的一条是 ToolMessage 且
        其 tool_call_id 等于该 AIMessage 第一个 tool_call 的 id
        (append_message 逐条落库的正常形态必满足)。
        """
        if not messages:
            return messages
        last = messages[-1]
        if not (isinstance(last, AIMessage) and last.tool_calls):
            return messages
        # 尾部 AIMessage 携带 tool_calls,却已无后续消息可与它配对
        logger.warning(
            "检测到尾部未配对的 AIMessage(tool_calls),已丢弃以防御毒历史(I-1):"
            " session_id=%s conversation_id=%s tool_call_ids=%s",
            session_id,
            conversation_id,
            [tc.get("id") for tc in last.tool_calls],
        )
        return messages[:-1]

    def append_message(self, session_id: str, message: BaseMessage) -> None:
        """把一条 LangChain 消息追加落库;不落库的类型直接跳过。"""
        if isinstance(message, HumanMessage):
            role, content, tool_calls, tool_call_id = (
                "user",
                message.content or "",
                None,
                None,
            )
        elif isinstance(message, AIMessage):
            role = "assistant"
            # 空 content(纯工具调用)存 None:NULL 表示「无文本内容」
            content = message.content or None
            # 只存 name/args/id 三键;"type" 由 AIMessage 构造时自动补齐
            tool_calls = (
                [
                    {"name": tc["name"], "args": tc["args"], "id": tc["id"]}
                    for tc in message.tool_calls
                ]
                if message.tool_calls
                else None
            )
            tool_call_id = None
        elif isinstance(message, ToolMessage):
            role, content, tool_calls, tool_call_id = (
                "tool",
                message.content or "",
                None,
                message.tool_call_id,
            )
        else:
            # SystemMessage 等其他类型不落库:system prompt 每轮现拼,不是历史
            return

        conversation_id = self.get_or_create_conversation(session_id)
        with get_session_local()() as session:
            with session.begin():
                session.add(
                    Message(
                        conversation_id=conversation_id,
                        role=role,
                        content=content,
                        tool_calls=tool_calls,
                        tool_call_id=tool_call_id,
                    )
                )

    @staticmethod
    def _row_to_message(row: Message) -> BaseMessage:
        """把一行 messages 记录还原成对应的 LangChain 消息。"""
        if row.role == "user":
            return HumanMessage(content=row.content or "")
        if row.role == "assistant":
            # content 为 NULL(纯工具调用)回填空串;tool_calls 三键构造
            # AIMessage 时会自动补 "type": "tool_call"
            return AIMessage(
                content=row.content or "",
                tool_calls=row.tool_calls or [],
            )
        if row.role == "tool":
            # tool_call_id 为 NULL 的脏数据兜底成空串:保证重建不炸是底线。
            # 注意空串 tool_call_id 的 ToolMessage 若原样发给 OpenAI API 仍会被拒,
            # 演示场景可接受,真实排障应修数据而非在这里编造 id。
            return ToolMessage(
                content=row.content or "",
                tool_call_id=row.tool_call_id or "",
            )
        raise ValueError(f"未知的消息 role:{row.role!r}")
