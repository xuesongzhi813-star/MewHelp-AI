"""明确型号/章节才能赋品类，不按答案里的随意提及强过滤。"""
import pytest
from app.kb.catalog import infer_product_category


@pytest.mark.parametrize('path,question,answer,expected', [
    ('商品规格 > MH-DJ101', '夹克尺寸', '棉100%', '牛仔夹克'),
    ('商品规格 > MH-SH202', '面料', '棉麻', '衬衫'),
    ('商品规格 > MH-PT302', '腰围', '76cm', '裤装'),
    ('商品规格 > MH-HD401', '尺码', 'M', '卫衣'),
    ('商品规格 > MH-JK502', '外套保养', '冷水', '运动外套'),
    ('商品规格 > MH-DJ999', '型号', '牛仔夹克', '通用'),
    ('售后 > 通用', '衣服能退吗', '牛仔夹克和衬衫都可能退', '通用'),
    ('规格 > 衬衫 > 材质', '成分', '棉', '衬衫'),
])
def test_product_category_uses_explicit_catalog_or_section(path, question, answer, expected):
    assert infer_product_category(path, question, answer) == expected
