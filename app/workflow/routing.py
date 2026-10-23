"""The classifier names an intent; code owns every exit."""
from langchain_core.messages import HumanMessage, SystemMessage

from app.workflow.contracts import IntentDecision
from app.config import get_settings
from app.workflow.router_prompts import INTENT_PROMPT

EXITS = {'物流': 'business', '订单': 'business', '售后': 'business',
         '商品咨询': 'knowledge', '退款退货': 'knowledge', '投诉': 'complaint', '闲聊': 'chat'}
CHAT_TEXT = '您好，我可以帮您解答古着商品、退换货政策，以及订单、物流和售后问题。'
COMPLAINT_TEXT = '很抱歉给您带来不好的体验。您可以选择转人工，或确认创建工单记录这次问题。'


def resolve_reference(question: str) -> str:
    return question


def route_intent(intent: str) -> str:
    return {**EXITS, '退款退货': 'refund', '售后': 'refund', '其他': 'other'}[IntentDecision(intent=intent, confidence=1.0).intent]


async def classify_intent(question: str, *, gateway, budget) -> IntentDecision:
    messages = [SystemMessage(content=INTENT_PROMPT), HumanMessage(content=question)]
    for attempt in range(2):
        try:
            result = IntentDecision.model_validate(await gateway.classify(messages, budget))
            if result.confidence < get_settings().intent_min_confidence:
                return IntentDecision(intent='其他', confidence=result.confidence)
            return result
        except ValueError:
            messages.append(SystemMessage(content='格式无效。只输出JSON，严格两个字段intent与confidence，类别必须选题目给定中文选项。'))
    return IntentDecision(intent='其他', confidence=0.0)
