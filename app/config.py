import json
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
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
    chat_workflow_enabled: bool = False
    intent_model: str | None = None
    intent_min_confidence: float = Field(default=0.7, ge=0, le=1, allow_inf_nan=False)
    # Seeds for a future chapter only. No small-model/upgrade execution path in ch06.
    intent_small_model: str | None = None
    intent_escalation_threshold: float = Field(default=0.7, ge=0, le=1, allow_inf_nan=False)
    agent_max_tool_calls: int = Field(default=6, gt=0)
    agent_token_budget: int = Field(default=12000, gt=0)
    agent_turn_timeout: float = Field(default=90, gt=0, le=300)
    agent_max_decisions: int = Field(default=10, gt=0)
    workflow_checkpoint_path: str = "log/ch05/checkpoints.sqlite"
    workflow_debug_view: bool = False
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
    # ch04 默认仍为旧配置；迁移验证后通过启动环境显式切换。
    kb_corpus_version: Literal['ch03', 'ch04'] = 'ch03'
    knowledge_strategy: Literal['dense', 'bm25', 'hybrid', 'hybrid_rerank'] = 'dense'
    hybrid_collection: str = 'knowledge_hybrid'
    kb_migration_manifest: str = 'log/kb/ch04-migration.json'
    rerank_model: str = 'BAAI/bge-reranker-v2-m3'
    rerank_base_url: str = 'https://api.siliconflow.cn/v1'
    rerank_api_key: str = ''
    rerank_min_score: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    query_timeout: float = Field(default=12, gt=0, le=300)
    rerank_timeout: float = Field(default=15, gt=0, le=300)
    embedding_timeout: float = Field(default=10, gt=0, le=300)
    knowledge_tool_timeout: float = Field(default=90, gt=0, le=300)
    answer_timeout: float = Field(default=45, gt=0, le=300)

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
