from langchain_core.runnables import Runnable

from app.config import Settings
from app.llm import build_chat_model, build_extractor
from app.schemas import AfterSalesInfo

SETTINGS = Settings(
    llm_base_url="https://api.deepseek.com",
    llm_api_key="sk-test",
    llm_model="deepseek-flash",
    llm_extra_body={"thinking": {"type": "disabled"}},
    shop_name="MewHelp 商城",
    agent_name="小喵",
)


def test_chat_model_carries_extra_body():
    llm = build_chat_model(SETTINGS)
    assert llm.extra_body == {"thinking": {"type": "disabled"}}
    assert llm.model_name == SETTINGS.llm_model


def test_chat_model_construction_params_come_from_settings():
    llm = build_chat_model(SETTINGS)
    assert llm.temperature == SETTINGS.llm_temperature
    assert llm.openai_api_base == SETTINGS.llm_base_url


def test_extractor_uses_function_calling():
    extractor = build_extractor(SETTINGS)
    assert isinstance(extractor, Runnable)
    method = extractor.first.kwargs["ls_structured_output_format"]["kwargs"]["method"]
    assert method == "function_calling"
    assert extractor.steps[-1].tools == [AfterSalesInfo]
