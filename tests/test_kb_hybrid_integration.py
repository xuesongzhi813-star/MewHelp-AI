"""只操作每次独占UUID集合；真实BM25、中文分词、前置过滤与RRF。"""
import uuid
import pytest

from app.kb import milvus
from app.kb.contracts import RetrievalFilters


@pytest.mark.integration
def test_native_chinese_bm25_hybrid_roundtrip():
    if not milvus.ping():
        pytest.skip('Milvus不可达；未验证原生BM25兼容性')
    name = 'ch04_test_' + uuid.uuid4().hex
    client = milvus.get_client()
    created = False
    try:
        analyzer = client.run_analyzer('MH-DJ101 牛仔夹克胸围', {'type': 'chinese'})
        assert analyzer
        milvus.ensure_hybrid_collection(name)
        created = True
        # 再次打开同一集合必须严格验证schema而非静默跳过。
        milvus.ensure_hybrid_collection(name)
        v1, v2 = [1.] + [0.] * 1023, [0., 1.] + [0.] * 1022
        milvus.upsert_records([
            {'id': '1', 'vector': v1, 'text': 'MH-DJ101 牛仔夹克 胸围110厘米',
             'category': '商品咨询', 'product_category': '牛仔夹克', 'content_type': '文档章节'},
            {'id': '2', 'vector': v2, 'text': 'MH-DJ102 牛仔夹克 胸围120厘米',
             'category': '商品咨询', 'product_category': '牛仔夹克', 'content_type': '文档章节'},
            {'id': '3', 'vector': v2, 'text': 'MH-SH202 衬衫 棉麻材质',
             'category': '商品咨询', 'product_category': '衬衫', 'content_type': '文档章节'},
        ], collection_name=name)
        client.flush(name)
        records = list(milvus.iter_records(name))
        assert {r['id'] for r in records} == {'1', '2', '3'}
        assert next(r for r in records if r['id'] == '1')['text'] == 'MH-DJ101 牛仔夹克 胸围110厘米'
        for sku, expected in [('MH-DJ101', '1'), ('MH-DJ102', '2')]:
            hits = milvus.search_bm25(sku, 50, collection_name=name, filters=RetrievalFilters())
            assert hits and hits[0][0] == expected
        hits = milvus.search_hybrid(v1, '胸围', collection_name=name,
                                    filters=RetrievalFilters(product_category='衬衫'))
        assert [row_id for row_id, _ in hits] == ['3']
    finally:
        # 只删本测试拥有的唯一名称；创建部分失败也可清理自身空集合。
        if created or client.has_collection(name):
            client.drop_collection(name)
