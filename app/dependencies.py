from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI

from app.config import get_settings
from app.llm import build_chat_model, build_extractor


def get_chat_model() -> ChatOpenAI:
    """FastAPI 依赖:提供配置好的 ChatOpenAI,测试可用 dependency_overrides 替换。"""
    return build_chat_model(get_settings())


def get_extractor() -> Runnable:
    """FastAPI 依赖:提供 function_calling 结构化提取器,测试可用 dependency_overrides 替换。"""
    return build_extractor(get_settings())
