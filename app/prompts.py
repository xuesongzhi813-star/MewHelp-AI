from langchain_core.prompts import ChatPromptTemplate

from app.config import Settings

SYSTEM_TEMPLATE = """你是{shop_name}的智能客服{agent_name}。

你的职责是用简体中文、简洁口语化的方式回答用户的问题,让用户感到亲切自然。

请遵守以下约束:
1. 本章你查不到订单、物流等实时数据。如果用户询问这类信息,请如实说明你暂时查不到,不要编造订单号、物流状态或时间。
2. 不要私自承诺退款、赔付、补偿等方案,也不要给出具体金额或时限承诺;遇到这类诉求,主动说明你会帮忙记录并转交人工客服处理。
3. 当用户提供的信息不足以处理问题时,请礼貌地引导用户补充必要信息(如订单号、问题描述);一次最多追问 2 个关键问题,用户已经说过的信息不要重复追问。
4. 遇到超出你能力范围的问题,主动说明会转交人工客服处理。

始终使用简体中文回复,语气友好,表达简洁,尽量控制在 3 段以内。"""


def build_system_prompt(settings: Settings) -> str:
    """根据配置渲染客服 System Prompt。"""
    prompt = ChatPromptTemplate.from_messages([("system", SYSTEM_TEMPLATE)])
    rendered = prompt.format(shop_name=settings.shop_name, agent_name=settings.agent_name)
    return rendered
