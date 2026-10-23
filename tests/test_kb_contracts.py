"""检索边界校验：禁止裸过滤表达式、未知品类及非法分数。"""
import pytest
from pydantic import ValidationError

from app.kb.contracts import AnswerDecision, Hit, QueryPlan, RetrievalFilters


@pytest.mark.parametrize('payload', [
    {'expr': 'id > 0'}, {'product_category': '智能猫砂盆'},
    {'category': 'x' * 256}, {'content_type': 'x' * 33},
])
def test_filters_reject_uncontrolled_input(payload):
    with pytest.raises(ValidationError):
        RetrievalFilters(**payload)


def test_plan_retains_explicit_filters_and_has_no_history_input():
    plan = QueryPlan(raw_query='不要衬衫', normalized_query='不要衬衫',
                     lexical_query='不要衬衫', intent='knowledge',
                     filters=RetrievalFilters(product_category='牛仔夹克'))
    assert plan.raw_query == '不要衬衫'
    assert plan.filters.product_category == '牛仔夹克'
    assert plan.filters.include_general is False
    with pytest.raises(ValidationError):
        QueryPlan(**{**plan.model_dump(), 'history': ['上轮说过MH-DJ101']})


def test_legacy_hit_import_and_positional_construction_stay_compatible():
    from app.kb.retriever import Hit as LegacyHit
    hit = LegacyHit(7, .8, '商品咨询', '尺码', '胸围110cm', '文档章节', '规格 > MH-DJ101')
    assert isinstance(hit, Hit)
    assert hit.chunk_id == 7 and hit.answer == '胸围110cm'
    assert hit.product_category == '通用' and hit.rerank_score is None


def test_answer_decision_rejects_invalid_citation_numbers():
    with pytest.raises(ValidationError):
        AnswerDecision(can_answer=True, answer='答[0]', reason='', used_citation_numbers=[0])
