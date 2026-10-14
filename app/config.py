import json
from functools import lru_cache
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_extra_body: Annotated[dict, NoDecode]
    llm_temperature: float = 0.7
    history_token_budget: int = 4000
    max_sessions: int = 1000
    shop_name: str
    agent_name: str

    @field_validator("llm_extra_body", mode="before")
    @classmethod
    def parse_llm_extra_body(cls, value: object) -> object:
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError(f"LLM_EXTRA_BODY 不是合法 JSON: {exc}") from exc
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()