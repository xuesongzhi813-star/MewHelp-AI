"""数据层基建测试:config 新字段(db_url/tool_timeout/faq_max_results)、
db 惰性单例工厂,以及 ORM 模型与真实 MySQL 表结构对齐(只读,不写库)。

模型对齐测试需要连接本机 MySQL melhelp 库(连接串来自 .env 的 DB_URL);
真实密码只存在于 .env,任何测试代码里都不出现。
"""

import pytest
import sqlalchemy
from sqlalchemy import create_engine, inspect

from app import models
from app.config import Settings, get_settings
from app.db import get_engine, get_session_local

REQUIRED_ENV = {
    "LLM_BASE_URL": "https://api.deepseek.com",
    "LLM_API_KEY": "sk-test",
    "LLM_MODEL": "deepseek-chat",
    "LLM_EXTRA_BODY": '{"thinking": {"type": "disabled"}}',
    "LLM_TEMPERATURE": "0.7",
    "HISTORY_TOKEN_BUDGET": "4000",
    "MAX_SESSIONS": "1000",
    "SHOP_NAME": "MewHelp 商城",
    "AGENT_NAME": "小喵",
}

# 模型 -> 真实表名,用于列对齐断言(防字段名漂移)
MODEL_BY_TABLE = {
    'low_confidence_questions':models.LowConfidenceQuestion,
    'review_queue':models.ReviewQueue,
    'eval_runs':models.EvalRun,
    "conversation_summaries": models.ConversationSummary,
    "conversations": models.Conversation,
    "faq": models.Faq,
    "messages": models.Message,
    "tickets": models.Ticket,
    "knowledge_chunks": models.KnowledgeChunk,
    "qa_extraction_staging": models.QaExtractionStaging,
}


@pytest.fixture
def env_settings(monkeypatch):
    """与 test_config.py 同款:必填项全走环境变量,测试不依赖磁盘 .env。"""
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ---------- config 新字段 ----------


def test_db_url_default_has_no_password(env_settings, monkeypatch):
    """默认 db_url 是无密码串;真实密码只能来自 .env 的 DB_URL。"""
    monkeypatch.delenv("DB_URL", raising=False)
    s = Settings(_env_file=None)  # 显式忽略 .env,验证纯默认值
    assert s.db_url == "mysql+pymysql://root@127.0.0.1:3306/melhelp"


def test_tool_timeout_default(env_settings, monkeypatch):
    """用 Settings(_env_file=None) 验证纯默认值,不依赖磁盘 .env 的取值。"""
    monkeypatch.delenv("TOOL_TIMEOUT", raising=False)
    s = Settings(_env_file=None)
    assert s.tool_timeout == 10


def test_tool_timeout_env_override(env_settings, monkeypatch):
    monkeypatch.setenv("TOOL_TIMEOUT", "7")
    get_settings.cache_clear()
    assert get_settings().tool_timeout == 7


def test_knowledge_settings_defaults(env_settings, monkeypatch):
    for key in ("EMBEDDING_API_KEY", "EMBEDDING_DIM", "KNOWLEDGE_MIN_SCORE",
                "MILVUS_URI", "TOOL_TIMEOUT"):
        monkeypatch.delenv(key, raising=False)
    s = Settings(_env_file=None)
    assert s.embedding_base_url == "https://api.siliconflow.cn/v1"
    assert s.embedding_model == "BAAI/bge-m3"
    assert s.embedding_dim == 1024
    assert s.embedding_batch_size == 32
    assert s.milvus_uri == "http://127.0.0.1:19530"
    assert s.knowledge_collection == "knowledge"
    assert s.knowledge_min_score == 0.5
    assert s.knowledge_dedup_score == 0.92
    assert s.kb_sources_dir == "data/kb"
    assert s.kb_max_markdown_bytes == 204800
    assert s.tool_timeout == 10


def test_faq_max_results_default(env_settings, monkeypatch):
    """用 Settings(_env_file=None) 验证纯默认值,不依赖磁盘 .env 的取值。"""
    monkeypatch.delenv("FAQ_MAX_RESULTS", raising=False)
    s = Settings(_env_file=None)
    assert s.faq_max_results == 3


def test_faq_max_results_env_override(env_settings, monkeypatch):
    monkeypatch.setenv("FAQ_MAX_RESULTS", "8")
    get_settings.cache_clear()
    assert get_settings().faq_max_results == 8


# ---------- db 惰性工厂 ----------


def test_create_engine_does_not_connect_when_mysql_unreachable():
    """create_engine 是惰性的:MySQL 不可达时构造引擎也不抛错(import app.db 安全)。"""
    # 端口 1 几乎必然没有 MySQL 在听,但引擎构造依然成功
    engine = create_engine("mysql+pymysql://root@127.0.0.1:1/melhelp")
    assert engine.url.database == "melhelp"


def test_engine_and_session_local_are_cached_singletons():
    e1 = get_engine()
    assert get_engine() is e1
    sl = get_session_local()
    assert get_session_local() is sl
    assert sl.kw["bind"] is e1
    # cache_clear 测试钩子可用:清理后重建得到新实例
    get_engine.cache_clear()
    get_session_local.cache_clear()
    try:
        assert get_engine() is not e1
    finally:
        get_engine.cache_clear()
        get_session_local.cache_clear()


# ---------- 模型与真实表对齐(只读,需真实 MySQL)----------


def _enum_values_by_column(columns) -> dict[str, list[str]]:
    """从列集合(模型列或反射列)提取 {列名: ENUM 值列表},非 ENUM 列跳过。

    模型列是 Column 对象(属性访问),反射列是 dict(下标访问),两种都兼容。
    """
    result = {}
    for col in columns:
        name = col["name"] if isinstance(col, dict) else col.name
        col_type = col["type"] if isinstance(col, dict) else col.type
        if isinstance(col_type, sqlalchemy.Enum):
            result[name] = list(col_type.enums)
    return result


def test_models_match_real_mysql_columns():
    """各模型 __table__.columns 键集合与真实表列名集合完全相等,防字段名漂移。"""
    insp = inspect(get_engine())
    for table_name, model in MODEL_BY_TABLE.items():
        real_cols = {col["name"] for col in insp.get_columns(table_name)}
        model_cols = set(model.__table__.columns.keys())
        assert model_cols == real_cols, (
            f"{table_name} 列漂移: "
            f"模型多出={sorted(model_cols - real_cols)}, "
            f"真实表多出={sorted(real_cols - model_cols)}"
        )


def test_models_match_real_mysql_enum_values():
    """模型 ENUM 列的取值列表与真实表逐列相等,防 DB 端 enum 漂移(否则写入才炸)。"""
    insp = inspect(get_engine())
    for table_name, model in MODEL_BY_TABLE.items():
        real_enums = _enum_values_by_column(insp.get_columns(table_name))
        model_enums = _enum_values_by_column(model.__table__.columns)
        assert model_enums == real_enums, (
            f"{table_name} ENUM 取值漂移: 模型={model_enums}, 真实表={real_enums}"
        )
