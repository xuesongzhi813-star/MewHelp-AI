from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI

from app.config import Settings
from app.schemas import AfterSalesInfo


def build_chat_model(settings: Settings) -> ChatOpenAI:
    """按 Settings 构造 ChatOpenAI,extra_body 在构造期注入(如 DeepSeek thinking 开关)。"""
    return ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        temperature=settings.llm_temperature,
        extra_body=settings.llm_extra_body,
    )


def build_extractor(settings: Settings) -> Runnable:
    """在 chat model 上挂结构化输出,用 function_calling 抽取售后信息。"""
    return build_chat_model(settings).with_structured_output(
        AfterSalesInfo, method="function_calling"
    )
