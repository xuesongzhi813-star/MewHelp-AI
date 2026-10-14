from langchain_core.runnables import Runnable

from app.config import get_settings
from app.dependencies import get_chat_model, get_extractor
from app.schemas import AfterSalesInfo

settings = get_settings()


def test_chat_model_carries_extra_body():
    llm = get_chat_model()
    assert llm.extra_body == {"thinking": {"type": "disabled"}}
    assert llm.model_name == settings.llm_model


def test_chat_model_construction_params_come_from_settings():
    llm = get_chat_model()
    assert llm.temperature == settings.llm_temperature
    assert llm.openai_api_base == settings.llm_base_url


def test_extractor_uses_function_calling():
    extractor = get_extractor()
    assert isinstance(extractor, Runnable)
    method = extractor.first.kwargs["ls_structured_output_format"]["kwargs"]["method"]
    assert method == "function_calling"
    assert extractor.steps[-1].tools == [AfterSalesInfo]
