"""只编译固定字段；值一律走Milvus参数绑定，不接受表达式。"""
from app.kb.contracts import RetrievalFilters


def compile_filter(filters: RetrievalFilters) -> tuple[str, dict]:
    clauses, values = [], {}
    if filters.category is not None:
        clauses.append('category == {category}')
        values['category'] = filters.category
    if filters.product_category is not None:
        categories = [filters.product_category]
        if filters.include_general and filters.product_category != '通用':
            categories.append('通用')
        clauses.append('product_category in {product_category}')
        values['product_category'] = categories
    if filters.content_type is not None:
        clauses.append('content_type == {content_type}')
        values['content_type'] = filters.content_type
    if filters.source_title is not None:
        clauses.append('(category == {source_title} or category like {source_prefix})')
        values['source_title'] = filters.source_title
        values['source_prefix'] = filters.source_title + ' > %'
    return ' and '.join(clauses), values
