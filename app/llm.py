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


def build_query_model(settings: Settings) -> Runnable:
    from app.kb.query import QueryRewrite

    return ChatOpenAI(
        model=settings.llm_model, api_key=settings.llm_api_key,
        base_url=settings.llm_base_url, temperature=0,
        extra_body=settings.llm_extra_body,
        timeout=settings.query_timeout, max_retries=0,
    ).with_structured_output(QueryRewrite, method="function_calling")


def build_answer_model(settings: Settings) -> Runnable:
    from app.kb.contracts import AnswerDecision

    return ChatOpenAI(
        model=settings.llm_model, api_key=settings.llm_api_key,
        base_url=settings.llm_base_url, temperature=0,
        extra_body=settings.llm_extra_body, timeout=settings.answer_timeout, max_retries=0,
    ).with_structured_output(AnswerDecision, method="function_calling", include_raw=True)


def build_judge_models(settings: Settings) -> tuple[Runnable, Runnable]:
    from app.kb.evaluation_judge import JudgeFactsOutput, RequiredFactsOutput

    base = ChatOpenAI(model=settings.llm_model, api_key=settings.llm_api_key,
        base_url=settings.llm_base_url, temperature=0, extra_body=settings.llm_extra_body,
        timeout=settings.answer_timeout, max_retries=0)
    return (base.with_structured_output(JudgeFactsOutput, method="function_calling", include_raw=True),
            base.with_structured_output(RequiredFactsOutput, method="function_calling", include_raw=True))
