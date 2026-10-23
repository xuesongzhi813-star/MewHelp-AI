"""演示 SKU 的确定品类映射；不从正文的随意提及推断过滤条件。"""
import re

SKU_CATEGORIES = {
    'MH-DJ101': '牛仔夹克', 'MH-DJ102': '牛仔夹克', 'MH-DJ103': '牛仔夹克',
    'MH-SH201': '衬衫', 'MH-SH202': '衬衫', 'MH-SH203': '衬衫',
    'MH-PT301': '裤装', 'MH-PT302': '裤装',
    'MH-HD401': '卫衣', 'MH-HD402': '卫衣',
    'MH-JK501': '运动外套', 'MH-JK502': '运动外套',
}
SKU_RE = re.compile(r'(?<![A-Z0-9])MH-[A-Z]{2}\d{3}(?![A-Z0-9])', re.I)


def infer_product_category(section_path: str, questions: str, answer: str) -> str:
    # 本文可能比较另一种商品；正文不能压过该块的明确章节归属。
    skus = SKU_RE.findall(section_path.upper())
    if not skus:
        skus = SKU_RE.findall(questions.upper())
    if skus:
        categories = {SKU_CATEGORIES.get(sku, '通用') for sku in skus}
        return next(iter(categories)) if len(categories) == 1 else '通用'
    categories = set(SKU_CATEGORIES.values()).intersection(
        part.strip() for part in section_path.split('>')
    )
    return next(iter(categories)) if len(categories) == 1 else '通用'
