"""只读单轮 Query 理解：保真保护、意图闸门、检索侧有限扩展。"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict

from app.config import get_settings
from app.kb.catalog import SKU_CATEGORIES, SKU_RE
from app.kb.contracts import QueryPlan, RetrievalFilters
from app.prompts import QUERY_UNDERSTANDING_PROMPT

# 超时只放弃本次结果；迟到线程无池写入、引用或全局结果副作用。
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="query-understanding")


class QueryRewrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    normalized_query: str
    intent: Literal["knowledge", "business", "chat"]
    reason: str = ""


SYNONYMS = (
    ("运费", "邮费", "配送费"), ("材质", "面料", "料子"),
    ("退货", "退回", "退货申请"), ("养护", "保养", "洗涤"),
    ("胸围", "胸宽", "平铺胸围"), ("到账", "入账", "退款到账"),
)
_NEGATION = re.compile(r"不要|不是|不能|不可以|不得|不许|不承诺|不保证|不用|不含|不支持|非纯棉|没有|未曾|并非|除外|排除")
_ORDER_ID = re.compile(r"(?<![A-Z0-9])ORD\d+(?![A-Z0-9])", re.I)
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_AMOUNT = re.compile(r"(?:¥\s*)?\d+(?:\.\d+)?\s*(?:元|块|人民币|%)")
_DURATION = re.compile(r"\d+(?:\.\d+)?\s*(?:小时|个月|分钟|天|年|月|周|日)(?:以内|之内|内|后|前)?")
_COUNT = re.compile(r"\d+\s*(?:件|条|次|个)")
_ORIGIN = re.compile(r"(?:签收|付款|下单|申请|审核|发货|退款|受理|收货)(?:之日起|之日|之后|后|起)")
_RELATIVE = re.compile(r"明天|今天|后天|昨天|下周|本周|下个月|本月")
_PRONOUN = re.compile(r"那个|这个|那件|这件|它|刚才|上面")
_KNOWLEDGE = re.compile(r"退货|换货|运费|邮费|包邮|积分|会员|发票|材质|面料|胸围|腰围|衣长|养护|保养|洗|到账|保证|承诺|成色|瑕疵|真伪|防水|签收|付款后|发货时效|多久发货")
_POLICY_KNOWLEDGE = re.compile(r"退货|换货|运费|邮费|包邮|积分|会员|发票|材质|面料|胸围|腰围|衣长|养护|保养|洗|到账|保证|承诺|成色|瑕疵|真伪|防水|发货时效|多久发货")


def normalize_query(text: str) -> str:
    normalized = " ".join(unicodedata.normalize("NFKC", text).split())
    return SKU_RE.sub(lambda m: m.group().upper(), normalized)


def validate_rewrite(raw_query: str, normalized_query: str) -> bool:
    raw, candidate = normalize_query(raw_query), normalize_query(normalized_query)
    if not candidate:
        return not raw
    if Counter(SKU_RE.findall(raw)) != Counter(SKU_RE.findall(candidate)):
        return False
    if Counter(m.upper() for m in _ORDER_ID.findall(raw)) != Counter(m.upper() for m in _ORDER_ID.findall(candidate)):
        return False
    if _PRONOUN.search(raw) and not SKU_RE.search(raw) and candidate != raw:
        return False
    # 标识符内的数字单独已严格检查，避免把订单号误当金额。
    raw_numbers = _ORDER_ID.sub("", SKU_RE.sub("", raw))
    new_numbers = _ORDER_ID.sub("", SKU_RE.sub("", candidate))
    if Counter(_NUMBER.findall(raw_numbers)) != Counter(_NUMBER.findall(new_numbers)):
        return False
    for pattern in (_NEGATION, _AMOUNT, _DURATION, _COUNT, _ORIGIN, _RELATIVE):
        protected = Counter(re.sub(r"\s+", "", v) for v in pattern.findall(raw))
        actual = Counter(re.sub(r"\s+", "", v) for v in pattern.findall(candidate))
        if protected != actual:
            return False
    return True


def _intent(raw: str, candidate: str = "knowledge") -> str:
    # 明确业务操作优先；提到“订单”不等于用户请求自己的订单查询。
    if (re.search(r"找人工|转人工|人工客服|投诉", raw)
            or _ORDER_ID.search(raw)
            or re.search(r"(?:我的|查一下|查询).*(?:订单|物流)|(?:实时库存|还有货|有货吗)", raw)):
        return "business"
    if SKU_RE.search(raw) or _KNOWLEDGE.search(raw) or _PRONOUN.search(raw):
        return "knowledge"
    if re.fullmatch(r"(?:你好|您好|嗨|哈[哈]+|谢谢|感谢|再见|早上好|晚上好|早安|晚安)[呀啊哦呢！!。.,，\s]*(?:小喵)?", raw):
        return "chat"
    return "chat" if candidate == "chat" else "knowledge"


def _filters(raw: str) -> RetrievalFilters:
    skus = SKU_RE.findall(raw)
    categories = {SKU_CATEGORIES[sku] for sku in skus if sku in SKU_CATEGORIES}
    if skus and all(sku in SKU_CATEGORIES for sku in skus) and len(categories) == 1:
        return RetrievalFilters(product_category=next(iter(categories)), include_general=True)
    categories_pattern = "|".join(sorted(set(SKU_CATEGORIES.values()), key=len, reverse=True))
    explicit = re.search(r"(?:只看|仅看|品类为|类别为|分类为)\s*(" + categories_pattern + ")", raw)
    if explicit and not re.search(r"不要|不是|不只|不看|排除", raw[:explicit.end()]):
        return RetrievalFilters(product_category=explicit.group(1))
    return RetrievalFilters()


def _lexical(normalized: str) -> str:
    extra = []
    for group in SYNONYMS:
        if any(term in normalized for term in group):
            extra.extend(term for term in group if term not in normalized)
    return " ".join([normalized, *dict.fromkeys(extra)]).strip()


def build_query_plan(raw_query: str, *, filters: RetrievalFilters | None = None, model=None) -> QueryPlan:
    original = normalize_query(raw_query)
    selected_filters = filters if filters is not None else _filters(original)
    if not original:
        return QueryPlan(raw_query=raw_query, normalized_query="", lexical_query="", filters=selected_filters)
    reason, fallback = "", False
    rewritten, intent = original, _intent(original)
    future = None
    try:
        if model is None:
            from app.llm import build_query_model
            model = build_query_model(get_settings())
        future = _executor.submit(model.invoke, [SystemMessage(content=QUERY_UNDERSTANDING_PROMPT),
                                                HumanMessage(content=original)])
        response = future.result(timeout=get_settings().query_timeout)
        response = QueryRewrite.model_validate(response)
        if not validate_rewrite(original, response.normalized_query):
            fallback, reason = True, "改写未通过关键条件保真检查，使用本轮原话"
        else:
            rewritten = normalize_query(response.normalized_query)
            intent = _intent(original, response.intent)
            reason = response.reason
    except TimeoutError:
        fallback, reason = True, "改写超时，使用本轮原话"
    except Exception:
        fallback, reason = True, "改写服务或结构异常，使用本轮原话"
    finally:
        if future is not None:
            future.cancel()
    return QueryPlan(raw_query=raw_query, normalized_query=rewritten, lexical_query=_lexical(rewritten),
                     intent=intent, requires_knowledge=intent == 'knowledge' or bool(_POLICY_KNOWLEDGE.search(original)),
                     filters=selected_filters, rewrite_reason=reason, rewrite_fallback=fallback)


def build_understood_query_plan(question: str) -> QueryPlan:
    """The workflow already used history to understand this query; no second LLM."""
    return QueryPlan(raw_query=question, normalized_query=question, lexical_query=_lexical(question),
                     requires_knowledge=True, filters=_filters(question))
