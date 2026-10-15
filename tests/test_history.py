from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.history import InMemorySessionStore, estimate_tokens, trim_history


def _msg(index: int) -> HumanMessage:
    """构造一条 token 数确定的消息:40 个非 CJK 字符 = 10 token。"""
    return HumanMessage(content=f"{index:02d}" + "a" * 38)


def test_estimate_tokens_counts_cjk_and_other_separately():
    # CJK 2 字 + 其余 3 字符向上取整 1 = 3
    assert estimate_tokens("你好abc") == 3


def test_estimate_tokens_empty_string_is_zero():
    assert estimate_tokens("") == 0


def test_trim_history_keeps_newest_within_budget_in_chronological_order():
    history = [_msg(i) for i in range(6)]
    # 每条 10 token,预算 25 只能装下最新的 2 条
    kept = trim_history(history, budget=25)
    assert kept == [history[4], history[5]]


def test_trim_history_drops_system_message_which_is_not_part_of_history():
    # 接口约定:裁剪只吃历史,system 由调用方负责拼接
    system = SystemMessage(content="你是客服")
    history = [system, *[_msg(i) for i in range(3)]]
    kept = trim_history(history, budget=15)
    assert all(not isinstance(m, SystemMessage) for m in kept)
    assert kept == [history[3]]


def test_trim_history_empty_history_returns_empty():
    assert trim_history([], budget=100) == []


def test_trim_history_budget_zero_returns_empty():
    assert trim_history([_msg(0)], budget=0) == []


def test_trim_history_never_splits_a_message():
    # allow_partial=False:消息只整条保留或整条丢弃,绝不拦腰截断
    long_msg = HumanMessage(content="超长" + "消息" * 100)  # 200+ token
    short_msg = HumanMessage(content="短")
    kept = trim_history([long_msg, short_msg], budget=150)
    # 长消息(200+ token)整条被丢弃,不会留下前 150 token 的残片
    assert kept == [short_msg]
    assert all(m.content == "短" or len(m.content) > 200 for m in kept)


def test_get_history_for_unknown_session_returns_empty_list():
    store = InMemorySessionStore(max_sessions=10)
    assert store.get_history("missing") == []


def test_append_message_then_get_history_roundtrip():
    store = InMemorySessionStore(max_sessions=10)
    message = HumanMessage(content="你好")
    store.append_message("s1", message)
    assert store.get_history("s1") == [message]


def test_exceeding_max_sessions_evicts_oldest_session():
    store = InMemorySessionStore(max_sessions=2)
    store.append_message("s1", HumanMessage(content="1"))
    store.append_message("s2", AIMessage(content="2"))
    store.append_message("s3", HumanMessage(content="3"))
    assert store.get_history("s1") == []
    assert len(store.get_history("s2")) == 1
    assert len(store.get_history("s3")) == 1
