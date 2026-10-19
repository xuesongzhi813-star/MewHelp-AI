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
    # 数据库连接串:默认值不含密码,真实密码只存在于本地 .env 的 DB_URL,绝不入库
    db_url: str = "mysql+pymysql://root@127.0.0.1:3306/melhelp"
    tool_timeout: int = 10
    faq_max_results: int = 3
    # 向量化与语义检索(真实 EMBEDDING_API_KEY 只存在于本地 .env,绝不入库)
    embedding_base_url: str = "https://api.siliconflow.cn/v1"
    embedding_api_key: str = ""
    embedding_model: str = "BAAI/bge-m3"
    embedding_dim: int = 1024
    embedding_batch_size: int = 32
    milvus_uri: str = "http://127.0.0.1:19530"
    milvus_token: str = ""
    knowledge_collection: str = "knowledge"
    knowledge_min_score: float = 0.5
    knowledge_dedup_score: float = 0.92
    # 知识挖掘与录入
    mining_batch_size: int = 5
    mining_max_chars: int = 6000
    kb_sources_dir: str = "data/kb"
    kb_max_markdown_bytes: int = 204800
    job_log_dir: str = "log/acceptance"
    make_exe: str = "make"

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