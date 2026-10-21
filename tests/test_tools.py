"""业务工具集测试:五个 @tool 工具 + TOOL_REGISTRY 注册表。

关键设计:
- 三个 Mock 工具不连库,只断言 JSON 可解析、字段齐全、同参数结果稳定;
- query_faq 已切换为语义检索,只测试「委托给 retriever」与模型可见描述,
  不再连库或依赖种子数据;
- create_ticket 走真实 MySQL,用「外层事务 + SAVEPOINT」回滚:
  把 app.tools.get_session_local 替换成绑定外层事务连接的会话工厂,
  工具内部 session.begin() 因 join_transaction_mode="create_savepoint"
  只会开 SAVEPOINT,用例结束整体 rollback,不留任何脏数据
  (与 test_seed.py 的 tx_session 同一意图,这里是「注入」给工具)。
"""

import json
import re

import pytest
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

import app.tools as tools_mod
from app.db import get_engine
from app.executor import execute_tool
from app.models import Conversation, Ticket
from app.tools import TOOL_REGISTRY, get_tools, query_faq as query_faq_tool

# 各工具期望的**模型可见**参数名集合(用于 tool_call_schema 的 JSON schema 断言)
EXPECTED_ARGS: dict[str, set[str]] = {
    "query_order": {"order_id"},
    "query_product": {"product_name"},
    "query_logistics": {"order_id"},
    "query_faq": {"keyword"},
    "create_ticket": {"description", "ticket_type"},
}

# 各工具的**系统注入**参数(标了 InjectedToolArg,模型不可见,由编排层注入)
INJECTED_ARGS: dict[str, set[str]] = {
    "create_ticket": {"conversation_id"},
    "query_faq": {"query_plan"},
}


@pytest.fixture
def tools_tx(monkeypatch):
    """注入回滚会话工厂:create_ticket 的写操作最终随外层事务整体 rollback。

    真实引擎上开一条连接并显式 begin,再把绑定该连接的 sessionmaker
    (join_transaction_mode="create_savepoint")塞给 app.tools.get_session_local;
    yield 连接本身,用例内可再开 Session 做预插/断言(同一连接可见未提交数据)。
    """
    with get_engine().connect() as conn:
        outer = conn.begin()
        session_local = sessionmaker(
            bind=conn, join_transaction_mode="create_savepoint"
        )
        monkeypatch.setattr(tools_mod, "get_session_local", lambda: session_local)
        try:
            yield conn
        finally:
            outer.rollback()


# ---------- 三个 Mock 工具(不连库)----------


def test_query_order_returns_stable_json():
    """query_order:返回可解析 JSON、字段齐全,同一 order_id 两次调用结果稳定。"""
    first = TOOL_REGISTRY["query_order"].invoke({"order_id": "ORD-TEST-0001"})
    second = TOOL_REGISTRY["query_order"].invoke({"order_id": "ORD-TEST-0001"})
    assert first == second  # dict 缓存保证进程内稳定

    data = json.loads(first)
    assert data["order_id"] == "ORD-TEST-0001"
    assert data["status"] in tools_mod.ORDER_STATUSES
    assert data["product"] in tools_mod.PRODUCT_NAMES_POOL
    assert isinstance(data["amount"], float) and data["amount"] > 0
    # 下单时间格式 YYYY-MM-DD HH:MM:SS,且是过去的时间点
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", data["placed_at"])


def test_query_product_returns_stable_json():
    """query_product:返回可解析 JSON,价格/库存/是否有货字段齐全且自洽。"""
    result = TOOL_REGISTRY["query_product"].invoke({"product_name": "复古水洗牛仔夹克"})
    data = json.loads(result)
    assert data["product_name"] == "复古水洗牛仔夹克"
    assert isinstance(data["price"], float) and data["price"] > 0
    assert isinstance(data["stock"], int) and data["stock"] >= 0
    # in_stock 与 stock 语义一致:有库存即有货
    assert data["in_stock"] == (data["stock"] > 0)
    # 规格(尺码 + 颜色)字段从预设池抽取
    assert data["specs"] in tools_mod.PRODUCT_SPECS_POOL


def test_query_logistics_returns_stable_json():
    """query_logistics:返回可解析 JSON,快递公司/节点/预计送达日期齐全。"""
    result = TOOL_REGISTRY["query_logistics"].invoke({"order_id": "ORD-TEST-0002"})
    data = json.loads(result)
    assert data["order_id"] == "ORD-TEST-0002"
    assert data["carrier"] in tools_mod.LOGISTICS_CARRIERS
    assert data["current_node"] in tools_mod.LOGISTICS_NODES
    # 预计送达日期格式合法(具体日期不约束,随机生成)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", data["eta"])


# ---------- query_faq(委托语义检索)----------


def test_query_faq_delegates_to_retriever(monkeypatch):
    """query_faq 不再查 MySQL,原样把 keyword 交给 retrieve 并返回其结果。"""
    seen: list[str] = []
    monkeypatch.setattr(
        tools_mod,
        "retrieve",
        lambda q: seen.append(q) or "1. 问\n→ 答",
    )

    assert query_faq_tool.invoke({"keyword": "邮费怎么算"}) == "1. 问\n→ 答"
    assert seen == ["邮费怎么算"]


def test_query_faq_description_mentions_full_question():
    """工具描述引导模型传用户原话/完整问句,而非单个关键词。"""
    schema = convert_to_openai_tool(query_faq_tool)
    description = schema["function"]["description"]
    assert "完整问句" in description or "用户原话" in description


# ---------- create_ticket(真实连库,回滚)----------


def _seed_conversation(conn, user_id: str) -> int:
    """在同一回滚事务里建一条真实 Conversation,返回其自增 id。"""
    with Session(conn) as session:
        with session.begin():
            conversation = Conversation(user_id=user_id)
            session.add(conversation)
            session.flush()
            return conversation.id


def test_create_ticket_success_and_format(tools_tx):
    """create_ticket:返回成功文案与合法工单号,行落表且 status='待处理',
    且 conversation_id 精确等于注入值。"""
    conversation_id = _seed_conversation(tools_tx, "ticket-success")

    result = tools_mod.create_ticket.invoke(
        {
            "description": "订单 ORD-TEST-0001 的商品有破损,要求退货",
            "ticket_type": "售后",
            "conversation_id": conversation_id,
        }
    )
    match = re.search(r"工单创建成功,工单号 (T\d{12})", result)
    assert match, f"返回文案缺少合法工单号:{result}"
    ticket_no = match.group(1)

    with Session(tools_tx) as session:
        row = session.get(Ticket, ticket_no)
        assert row is not None
        assert row.description == "订单 ORD-TEST-0001 的商品有破损,要求退货"
        assert row.ticket_type == "售后"
        assert row.status == "待处理"  # 默认值由模型/数据库口径一致保证
        # conversation_id 精确等于注入值(不再是「指向任意存在的会话」过渡口径)
        assert row.conversation_id == conversation_id


def test_create_ticket_missing_conversation_id_raises():
    """漏注入 conversation_id:invoke 当场抛 ValidationError,而非静默写脏外键。

    注入参数无默认值——编排层忘了注入时问题必须暴露(该路径在参数校验阶段
    即失败,不会触达真实库,故无需回滚 fixture)。
    """
    with pytest.raises(ValidationError):
        tools_mod.create_ticket.invoke(
            {"description": "缺少注入参数的工单", "ticket_type": "咨询"}
        )


def test_create_ticket_sequence_increments(tools_tx):
    """连续两次创建:序号严格递增(+1),工单号格式均为 ^T\\d{12}$。"""
    conversation_id = _seed_conversation(tools_tx, "ticket-seq")

    first = tools_mod.create_ticket.invoke(
        {"description": "第一次咨询测试", "ticket_type": "咨询", "conversation_id": conversation_id}
    )
    second = tools_mod.create_ticket.invoke(
        {"description": "第二次投诉测试", "ticket_type": "投诉", "conversation_id": conversation_id}
    )
    no1 = re.search(r"T(\d{12})", first).group(1)
    no2 = re.search(r"T(\d{12})", second).group(1)
    assert re.fullmatch(r"T\d{12}", f"T{no1}")
    assert re.fullmatch(r"T\d{12}", f"T{no2}")
    # 同日同前缀,尾号四位递增 1
    assert no1[:8] == no2[:8]
    assert int(no2[8:]) == int(no1[8:]) + 1


def test_execute_tool_injects_real_conversation_id_over_forged_arg(tools_tx):
    """端到端锁死执行器的**合并方向**:上游把同名 conversation_id 塞进 args 时,
    落库用的是 context 注入的真值。

    锁的是执行器 `{**args, **context}` 的合并顺序(context 在后覆盖 args),
    而非「模型能伪造 conversation_id」——该参数不在模型可见 schema 里,模型侧
    根本产不出它。若日后有人把合并顺序写反成 `{**context, **args}`,伪造值会
    静默落到 tickets 表(伪造 id 是合法存在的会话,FK 不报错),这条用例即变红;
    这正是删掉它会失去的保护。用真实 execute_tool(非直接 invoke)才覆盖合并逻辑。
    """
    real_id = _seed_conversation(tools_tx, "ticket-inject-real")
    forged_id = _seed_conversation(tools_tx, "ticket-inject-forged")

    result = execute_tool(
        "create_ticket",
        {
            "description": "端到端注入验证",
            "ticket_type": "投诉",
            "conversation_id": forged_id,  # 上游塞入的伪造值
        },
        context={"conversation_id": real_id},  # 系统注入的真值
    )

    assert "工单创建成功" in result
    ticket_no = re.search(r"工单创建成功,工单号 (T\d{12})", result).group(1)

    with Session(tools_tx) as session:
        row = session.get(Ticket, ticket_no)
        assert row is not None
        # 落表用的是 context 注入的真值,而非 args 里的伪造值
        assert row.conversation_id == real_id
        assert row.conversation_id != forged_id
        # 伪造 id 在 tickets 表里查不到任何行
        forged_rows = session.scalars(
            select(Ticket).where(Ticket.conversation_id == forged_id)
        ).all()
        assert forged_rows == []


# ---------- TOOL_REGISTRY 注册表 ----------


def test_tool_registry_contains_all_five_tools():
    """注册表五工具齐,键与工具名一一对应。"""
    assert set(TOOL_REGISTRY) == set(EXPECTED_ARGS)
    for name, instance in TOOL_REGISTRY.items():
        assert instance.name == name  # @tool 默认取函数名


def test_tool_registry_wraps_original_functions():
    """每个 StructuredTool 包的是同名原函数(docstring 即工具描述)。"""
    for name, instance in TOOL_REGISTRY.items():
        assert instance.func.__name__ == name
        assert instance.description  # 描述非空:模型靠它决定何时调用


def test_tool_args_schema_properties_match_params():
    """模型可见 schema 与完整 args_schema 双口径断言:

    - tool_call_schema(模型侧真正看到的):properties/required 恰好等于
      EXPECTED_ARGS(系统注入参数已被 InjectedToolArg 摘除);
    - args_schema(工具内部完整 schema):properties/required 恰好等于
      EXPECTED_ARGS ∪ INJECTED_ARGS(注入参数在 args_schema 里存在)。

    两个口径一起锁住「注入参数在 args_schema 里存在」且「不进模型可见 schema」。
    每个参数的 description 非空(模型靠它决定怎么传参)。
    """
    for name, instance in TOOL_REGISTRY.items():
        visible = instance.tool_call_schema.model_json_schema()
        visible_props = visible["properties"]
        assert set(visible_props) == EXPECTED_ARGS[name], f"{name} 模型可见参数集合不符"
        for prop_name, prop in visible_props.items():
            assert prop.get("description"), f"{name}.{prop_name} 缺少 description"
        # 必填参数全部进 required(五个工具的模型可见入参都是必填)
        assert set(visible["required"]) == EXPECTED_ARGS[name]

        full = instance.args_schema.model_json_schema()
        expected_full = EXPECTED_ARGS[name] | INJECTED_ARGS.get(name, set())
        assert set(full["properties"]) == expected_full, f"{name} 完整参数集合不符"
        expected_required = expected_full - ({"query_plan"} if name == "query_faq" else set())
        assert set(full["required"]) == expected_required
    # create_ticket 的 ticket_type 是枚举,取值与业务口径一致
    ticket_type_prop = TOOL_REGISTRY["create_ticket"].args_schema.model_json_schema()[
        "properties"
    ]["ticket_type"]
    assert set(ticket_type_prop["enum"]) == {"售后", "投诉", "咨询"}


def test_create_ticket_openai_payload_hides_injected_arg():
    """最终口径:真正发给 DeepSeek 的报文里,conversation_id 不出现在
    parameters.properties 与 required 中——模型完全看不到该参数。

    与上一个测试互补:上一个锁 args_schema 层,这里锁 convert_to_openai_tool
    转换后的实际请求报文层。
    """
    payload = convert_to_openai_tool(TOOL_REGISTRY["create_ticket"])
    params = payload["function"]["parameters"]
    assert "conversation_id" not in params["properties"]
    assert "conversation_id" not in params["required"]
    # 对照组:模型可见参数仍在(证明摘除的是注入参数,而非整个 schema 出错)
    assert set(params["properties"]) == EXPECTED_ARGS["create_ticket"]


def test_get_tools_returns_registry_values():
    """get_tools() 返回注册表全部实例,供 bind_tools 使用。"""
    tools = get_tools()
    assert len(tools) == 5
    # StructuredTool 不可哈希,按工具名对比(顺序与注册表一致)
    assert [t.name for t in tools] == list(TOOL_REGISTRY)
