"""数据库基建:引擎与会话工厂(SQLAlchemy 2.0 风格)。

create_engine 是惰性的(构造时不连库),因此 MySQL 不可达时 import 本模块不会抛错;
真正建连发生在第一次使用引擎时。get_engine/get_session_local 用 lru_cache
提供模块级单例,风格与 app.config.get_settings 一致,并保留 cache_clear 测试钩子。
"""

from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import get_settings


@lru_cache
def get_engine():
    """惰性创建模块级单例引擎;pool_pre_ping 避免长连接被 MySQL 掐断后报错。"""
    return create_engine(get_settings().db_url, pool_pre_ping=True)


@lru_cache
def get_session_local():
    """会话工厂单例,供后续 FastAPI 依赖注入与仓储层使用。"""
    return sessionmaker(bind=get_engine(), expire_on_commit=False)
