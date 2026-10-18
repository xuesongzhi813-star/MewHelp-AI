"""ch02 业务工具集:五个 @tool 工具 + TOOL_REGISTRY 注册表。

分工:
- query_order / query_product / query_logistics:Mock 数据——模块级 random.Random
  随机生成,dict 缓存保证「同一参数同进程内结果稳定」;返回
  json.dumps(ensure_ascii=False) 的 JSON 字符串。
- query_faq:真实查 faq 表,question/answer 双列 LIKE 模糊匹配,
  条数上限取 Settings.faq_max_results;LIKE 通配符(% _ /)按字面量处理
  (contains(autoescape=True) 自动转义并附带 ESCAPE 子句)。
- create_ticket:真实写 tickets 表,工单号 = T + YYYYMMDD + 4 位零填充当日序号。
  其 conversation_id 是**系统注入参数**(InjectedToolArg):从模型可见 schema
  里摘除,值由对话编排层通过 execute_tool 的 context 提供——工具拿不到会话
  上下文,不能自己猜关联哪条会话。

测试钩子(与 seed.py 同款做法):工具内部一律通过模块级 get_session_local /
get_settings 名字拿会话与配置,monkeypatch app.tools.* 即可注入带外层事务的
回滚会话或假配置,不需要真实写库。
"""

import json
import random
from datetime import date, datetime, timedelta
from typing import Annotated, Literal

from langchain_core.tools import BaseTool, InjectedToolArg, tool
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session_local
from app.models import Faq, Ticket

# ---------------------------------------------------------------------------
# Mock 数据源(订单/商品/物流):进程内共享一个随机源即可,不需要跨进程可复现
# ---------------------------------------------------------------------------

_RNG = random.Random()

# 订单状态池(与需求口径一致:已付款/已发货/已签收/退款中)
ORDER_STATUSES: tuple[str, ...] = ("已付款", "已发货", "已签收", "退款中")
# 商品名池:二手古着店主打的复古服饰品类
PRODUCT_NAMES_POOL: tuple[str, ...] = (
    "复古水洗牛仔夹克",
    "美式印花夏威夷衬衫",
    "灯芯绒高腰阔腿裤",
    "做旧款连帽卫衣",
    "复古运动拉链外套",
    "高腰直筒牛仔裤",
)
# 物流公司池(顺丰/中通/圆通)与物流节点池
LOGISTICS_CARRIERS: tuple[str, ...] = ("顺丰速运", "中通快递", "圆通速递")
LOGISTICS_NODES: tuple[str, ...] = ("已揽收", "运输中", "到达转运中心", "派送中", "已签收")
# 商品规格池(二手古着:尺码 + 颜色)
PRODUCT_SPECS_POOL: tuple[str, ...] = (
    "S码 黑色",
    "M码 米白",
    "L码 藏青",
    "XL码 咖色",
    "均码 复古蓝",
    "41码 灰白",
)

# 「同一参数同进程内稳定」的简单 dict 缓存:order_id / product_name -> JSON 字符串
_order_cache: dict[str, str] = {}
_product_cache: dict[str, str] = {}
_logistics_cache: dict[str, str] = {}


# ---------------------------------------------------------------------------
# 参数 schema:模型靠 Field description 决定怎么传参,全部中文写实
# ---------------------------------------------------------------------------


class QueryOrderArgs(BaseModel):
    """query_order 的入参 schema。"""

    order_id: str = Field(description="订单号,用户消息中提到的订单编号,例如 ORD202610150012")


class QueryProductArgs(BaseModel):
    """query_product 的入参 schema。"""

    product_name: str = Field(description="商品名称,尽量使用用户原话中的完整商品名,例如 复古水洗牛仔夹克")


class QueryLogisticsArgs(BaseModel):
    """query_logistics 的入参 schema。"""

    order_id: str = Field(description="订单号,用户想查询物流进度的订单编号")


class QueryFaqArgs(BaseModel):
    """query_faq 的入参 schema。"""

    keyword: str = Field(description="FAQ 检索关键词,单个词效果最好,例如 退货、发票、尺码、发货、积分、优惠券")


class CreateTicketArgs(BaseModel):
    """create_ticket 的入参 schema。"""

    description: str = Field(description="工单内容:完整转述用户的问题,包含订单号、商品名等关键上下文")
    ticket_type: Literal["售后", "投诉", "咨询"] = Field(
        description="工单类型:售后(退换货/维修类)、投诉(不满/纠纷类)、咨询(其他疑问类)"
    )
    # InjectedToolArg:从模型可见 schema 摘除(模型不填),值由编排层注入。
    # 故意不给默认值——漏注入时 invoke 当场抛 ValidationError,而非静默写脏外键。
    conversation_id: Annotated[int, InjectedToolArg] = Field(
        description="当前会话主键(conversations.id),由系统注入,模型不要填"
    )


# ---------------------------------------------------------------------------
# 工具一:query_order(Mock)
# ---------------------------------------------------------------------------


@tool(args_schema=QueryOrderArgs)
def query_order(order_id: str) -> str:
    """根据订单号查询订单信息:当前状态、商品、实付金额与下单时间。

    用户询问「我的订单怎么样了/到哪了/退款进度」等订单类问题时调用;
    输入订单号,返回 JSON 文本;查物流轨迹请改用 query_logistics。
    """
    if order_id not in _order_cache:
        placed_at = datetime.now() - timedelta(
            days=_RNG.randint(0, 30), minutes=_RNG.randint(0, 1439)
        )
        data = {
            "order_id": order_id,
            "status": _RNG.choice(ORDER_STATUSES),
            "product": _RNG.choice(PRODUCT_NAMES_POOL),
            # 金额保留两位小数,单位元
            "amount": round(_RNG.uniform(59, 899), 2),
            "placed_at": placed_at.strftime("%Y-%m-%d %H:%M:%S"),
        }
        _order_cache[order_id] = json.dumps(data, ensure_ascii=False)
    return _order_cache[order_id]


# ---------------------------------------------------------------------------
# 工具二:query_product(Mock)
# ---------------------------------------------------------------------------


@tool(args_schema=QueryProductArgs)
def query_product(product_name: str) -> str:
    """根据商品名称查询商品信息:售价、库存数量与当前是否有货。

    用户询问「XX 多少钱/还有货吗/库存多少」等商品类问题时调用;
    输入商品名称,返回 JSON 文本。
    """
    if product_name not in _product_cache:
        stock = _RNG.randint(0, 200)
        data = {
            "product_name": product_name,
            "price": round(_RNG.uniform(29, 599), 2),
            "stock": stock,
            "in_stock": stock > 0,
            "specs": _RNG.choice(PRODUCT_SPECS_POOL),
        }
        _product_cache[product_name] = json.dumps(data, ensure_ascii=False)
    return _product_cache[product_name]


# ---------------------------------------------------------------------------
# 工具三:query_logistics(Mock)
# ---------------------------------------------------------------------------


@tool(args_schema=QueryLogisticsArgs)
def query_logistics(order_id: str) -> str:
    """根据订单号查询物流信息:承运快递公司、当前配送节点与预计送达日期。

    用户询问「包裹到哪了/什么时候能送到/是什么快递」等物流类问题时调用;
    输入订单号,返回 JSON 文本;查订单状态/金额请改用 query_order。
    """
    if order_id not in _logistics_cache:
        eta = date.today() + timedelta(days=_RNG.randint(1, 7))
        data = {
            "order_id": order_id,
            "carrier": _RNG.choice(LOGISTICS_CARRIERS),
            "current_node": _RNG.choice(LOGISTICS_NODES),
            "eta": eta.strftime("%Y-%m-%d"),
        }
        _logistics_cache[order_id] = json.dumps(data, ensure_ascii=False)
    return _logistics_cache[order_id]


# ---------------------------------------------------------------------------
# 工具四:query_faq(真实查库)
# ---------------------------------------------------------------------------


@tool(args_schema=QueryFaqArgs)
def query_faq(keyword: str) -> str:
    """检索店铺常见问题知识库(FAQ),返回与关键词最相关的问答条目。

    用户咨询退货换货政策、发货时效、修改地址、发票、支付方式、优惠券、
    会员积分、尺码选择等常见问题时优先调用;返回「问题→答案」多行文本,
    检索不到时返回明确的「无匹配」提示,此时应考虑安抚用户或转人工。
    """
    # 条数上限运行时读取(而非 import 时固化),测试 monkeypatch get_settings 即可改
    max_results = get_settings().faq_max_results
    # autoescape=True:keyword 里的 % _ / 一律按字面量转义,不当 LIKE 通配符
    with get_session_local()() as session:
        with session.begin():
            rows = session.scalars(
                select(Faq)
                .where(
                    Faq.question.contains(keyword, autoescape=True)
                    | Faq.answer.contains(keyword, autoescape=True)
                )
                .order_by(Faq.id.asc())
                .limit(max_results)
            ).all()
            # 在 session 上下文内拼好文本:commit 会使 ORM 实例属性过期,
            # 离开上下文后再访问会触发 DetachedInstanceError
            if not rows:
                return f'FAQ 库中没有找到与「{keyword}」相关的条目'
            entries = [
                f"{i}. {row.question}\n→ {row.answer}" for i, row in enumerate(rows, 1)
            ]
    return "\n\n".join(entries)


# ---------------------------------------------------------------------------
# 工具五:create_ticket(真实写库)
# ---------------------------------------------------------------------------


def _next_ticket_no(session: Session) -> str:
    """生成当日工单号:T + YYYYMMDD + 4 位零填充序号。

    序号 = 当日已有最大 ticket_no 的尾号 + 1,当日无工单则从 0001 起。
    前缀只含字母与数字,无 LIKE 通配符,startswith 拼前缀查询安全。
    """
    prefix = f"T{date.today():%Y%m%d}"
    last = session.scalar(
        select(func.max(Ticket.ticket_no)).where(Ticket.ticket_no.startswith(prefix))
    )
    seq = int(last[len(prefix):]) + 1 if last else 1
    return f"{prefix}{seq:04d}"


@tool(args_schema=CreateTicketArgs)
def create_ticket(
    description: str,
    ticket_type: Literal["售后", "投诉", "咨询"],
    conversation_id: Annotated[int, InjectedToolArg],
) -> str:
    """为用户创建人工客服工单,成功后返回工单号。

    当用户明确要求转人工、或问题超出常见问题/订单/物流查询能力
    (如复杂售后纠纷、投诉)时调用;description 需完整转述用户问题。

    conversation_id 是系统注入参数(模型不可见):由对话编排层经
    execute_tool 的 context 传入真实会话主键,工单据此挂到正确会话上。
    """
    with get_session_local()() as session:
        with session.begin():
            ticket_no = _next_ticket_no(session)
            session.add(
                Ticket(
                    ticket_no=ticket_no,
                    conversation_id=conversation_id,
                    description=description,
                    ticket_type=ticket_type,
                )
            )
    # with session.begin() 退出时已 commit,此处直接回报工单号
    return f"工单创建成功,工单号 {ticket_no}"


# ---------------------------------------------------------------------------
# 注册表:工具名字符串 -> 工具实例
# ---------------------------------------------------------------------------

TOOL_REGISTRY: dict[str, BaseTool] = {
    "query_order": query_order,
    "query_product": query_product,
    "query_logistics": query_logistics,
    "query_faq": query_faq,
    "create_ticket": create_ticket,
}


def get_tools() -> list[BaseTool]:
    """全部业务工具列表,供 LLM bind_tools 使用。"""
    return list(TOOL_REGISTRY.values())
