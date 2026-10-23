"""检索与生成的共享边界，不导入客户端或检索编排。"""
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

RetrievalStrategy = Literal['dense', 'bm25', 'hybrid', 'hybrid_rerank']
ProductCategory = Literal['通用', '牛仔夹克', '衬衫', '裤装', '卫衣', '运动外套']


class RetrievalFilters(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    category: str | None = Field(default=None, max_length=255)
    product_category: ProductCategory | None = None
    content_type: str | None = Field(default=None, max_length=32)
    include_general: bool = False
    source_title: str | None = Field(default=None, max_length=255, pattern=r'^[^%_]+$')


class QueryPlan(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    raw_query: str
    normalized_query: str
    lexical_query: str
    intent: Literal['knowledge', 'chat', 'business'] = 'knowledge'
    requires_knowledge: bool = False
    filters: RetrievalFilters = Field(default_factory=RetrievalFilters)
    rewrite_reason: str = ''
    rewrite_fallback: bool = False


@dataclass(frozen=True)
class Hit:
    chunk_id: int
    score: float
    category: str
    questions: str
    answer: str
    content_type: str
    section_path: str
    product_category: str = '通用'
    score_type: str = 'dense'
    dense_score: float | None = None
    bm25_score: float | None = None
    fusion_score: float | None = None
    rerank_score: float | None = None


@dataclass(frozen=True)
class RetrievalResult:
    plan: QueryPlan
    strategy: RetrievalStrategy
    hits: tuple[Hit, ...] = ()
    candidate_ids: tuple[int, ...] = ()
    reason: str = ''


@dataclass(frozen=True)
class Evidence:
    number: int
    hit: Hit


class AnswerDecision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    can_answer: bool
    reason: str
    answer: str
    used_citation_numbers: list[Annotated[int, Field(ge=1, le=10)]] = Field(default_factory=list)
